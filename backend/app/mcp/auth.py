"""Bearer authentication for the MCP endpoint: the same tenant JWT as the REST API.

`TenantAuthMiddleware` wraps the MCP ASGI app. Every request to `/mcp` must carry
`Authorization: Bearer <JWT>`, checked by the API's own `authenticate_token` (signature,
`exp`, `iss`, and the tenant must still exist under the same name). A failure is the
API's 401 Problem Details with `WWW-Authenticate: Bearer`, or 503 if no signing key is
configured. On success the tenant goes into a context variable that the tools read with
`current_tenant_id()`: the tenant is never a tool argument.

Why not the SDK's OAuth resource-server support: it advertises an OAuth authorization
server (`issuer_url`) that clients would try to discover and use, and this app has none
(tokens are issued per tenant). Clients send the token as a static header (ADR-017).

The context variable reaches the tools because the server runs in stateless mode: each
HTTP request handles its own MCP messages, in tasks started from this request's context.
"""

import contextvars
import logging
import uuid

from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.deps import authenticate_token
from app.api.problem import problem_response
from app.core.config import Settings
from app.core.errors import AppError, Unauthorized

logger = logging.getLogger(__name__)

_tenant: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "mcp_tenant", default=None
)

_MISSING = "Missing or invalid credentials."


class TenantAuthMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings, factory: sessionmaker[Session]) -> None:
        self._app = app
        self._settings = settings
        self._factory = factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        try:
            token = _bearer_token(scope)
            # Blocking (a SELECT on `tenants`): off the event loop.
            tenant = await run_in_threadpool(
                authenticate_token, self._settings, self._factory, token
            )
        except AppError as exc:
            headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, Unauthorized) else None
            response = problem_response(
                exc.status, exc.title, exc.detail, exc.type, headers=headers
            )
            await response(scope, receive, send)
            return
        reset = _tenant.set(tenant.id)
        try:
            await self._app(scope, receive, send)
        finally:
            _tenant.reset(reset)


def current_tenant_id() -> uuid.UUID:
    """The tenant of the authenticated MCP request. Raises `ToolError` without one, which
    only happens if the server were served without `TenantAuthMiddleware`."""
    tenant = _tenant.get()
    if tenant is None:
        raise ToolError("Not authenticated.")
    return tenant


def _bearer_token(scope: Scope) -> str:
    for name, value in scope["headers"]:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
            break
    # The same event as the REST API logs for a missing header.
    logger.info("auth.rejected", extra={"reason": "missing_bearer"})
    raise Unauthorized(_MISSING)
