"""FastAPI dependencies shared by the routers.

Contract (routers and tests rely on these names and signatures):
- `get_current_tenant` -> `TenantContext`: verifies the `Authorization: Bearer <JWT>`
  header (signature, `exp`, `iss`) and that the tenant exists; raises `Unauthorized`.
  The tenant comes ONLY from the verified token, never from the request.
- `get_tenant_session` -> `Session`: a session inside `tenant_transaction` for the current
  tenant (RLS applies); commits when the request handler returns, rolls back on error.
- `get_storage` -> `Storage`, `get_dispatcher` -> `TaskDispatcher`: overridable in tests.
- `get_session_factory` -> the app-role `sessionmaker`, for endpoints that need to control
  their own transactions (the upload commits before enqueueing).
"""

import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core import tracing
from app.core.auth import AuthConfigError, verify_token
from app.core.config import Settings, get_settings
from app.core.errors import ServiceUnavailable, Unauthorized
from app.db.models import Tenant
from app.db.session import session_factory_for, tenant_transaction
from app.ingestion.storage import LocalDiskStorage
from app.ports import Storage, TaskDispatcher

logger = logging.getLogger(__name__)

# `auto_error=False`: a missing or non-Bearer header is reported as our own 401 Problem
# Details instead of FastAPI's default 403. It also adds the "Authorize" button to /docs.
_bearer = HTTPBearer(auto_error=False)

# The same message for every authentication failure: the client is not told whether the
# signature, the expiry or the tenant was wrong (the logs say which).
_UNAUTHORIZED_DETAIL = "Missing or invalid credentials."


@dataclass(frozen=True)
class TenantContext:
    id: uuid.UUID
    name: str


def get_session_factory(
    settings: Annotated[Settings, Depends(get_settings)],
) -> sessionmaker[Session]:
    """Sessions with the app role (`rutaviva_app`): RLS applies to them."""
    return session_factory_for(settings.database_url)


def get_current_tenant(
    settings: Annotated[Settings, Depends(get_settings)],
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TenantContext:
    if credentials is None:
        logger.info("auth.rejected", extra={"reason": "missing_bearer"})
        raise Unauthorized(_UNAUTHORIZED_DETAIL)

    try:
        claims = verify_token(settings, credentials.credentials)
    except AuthConfigError:
        # The server cannot verify any token: a configuration problem, not the client's.
        logger.exception("auth.key_unavailable")
        raise ServiceUnavailable("Authentication is not configured.") from None
    except jwt.InvalidTokenError as exc:
        # The exception type says why (ExpiredSignatureError, InvalidIssuerError...).
        logger.info("auth.rejected", extra={"reason": type(exc).__name__})
        raise Unauthorized(_UNAUTHORIZED_DETAIL) from None

    # `tenants` has no RLS and the app role can SELECT it: this runs before any tenant
    # is set. The name must match too, so a token issued before a re-seed stops working.
    with factory() as session:
        tenant = session.scalar(select(Tenant).where(Tenant.id == claims.tenant_id))
    if tenant is None or tenant.name != claims.tenant_name:
        logger.info(
            "auth.rejected",
            extra={"reason": "unknown_tenant", "tenant_id": str(claims.tenant_id)},
        )
        raise Unauthorized(_UNAUTHORIZED_DETAIL)

    tracing.set_attributes(**{tracing.TENANT_ID: str(tenant.id)})
    return TenantContext(id=tenant.id, name=tenant.name)


def get_tenant_session(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> Iterator[Session]:
    # Commits when the handler returns normally, rolls back if it raises.
    with tenant_transaction(factory, tenant.id) as session:
        yield session


# Use this alias in routers. `scope="function"` closes the transaction (commit) before the
# response is sent, so a failed commit becomes an error response instead of a 200 for
# data that was never saved. (FastAPI's default for `yield` dependencies ends them after
# the response.)
TenantSession = Annotated[Session, Depends(get_tenant_session, scope="function")]
CurrentTenant = Annotated[TenantContext, Depends(get_current_tenant)]


def get_storage(settings: Annotated[Settings, Depends(get_settings)]) -> Storage:
    return LocalDiskStorage(settings.files_dir)


def get_dispatcher() -> TaskDispatcher:
    # Imported here so the API (and its tests) never import Celery unless they enqueue.
    from app.worker.dispatch import CeleryDispatcher

    return CeleryDispatcher()
