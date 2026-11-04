"""Demo sign-in: one token per tenant at the click of a button (ADR-016).

A shortcut for a demo, NOT authentication: anyone who can reach the API can get a token
for any tenant while it is on. The token is the same one `make token` prints (same key,
claims and TTL, `issue_token`), so the API checks it exactly like any other and tenant
isolation (token-derived tenant, explicit filter, RLS) is unchanged.

On by default; `DEMO_LOGIN=false` (or `0`, `no`, `off`) turns it off, and then the router
is not registered at all: both routes answer 404 and are absent from OpenAPI. Read here
from the environment, on purpose outside `Settings`.

The only data these routes read is the `tenants` table (name and display name), which
the app role can read with no tenant set (no RLS on it, as in `get_current_tenant`).
"""

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session_factory
from app.core import tracing
from app.core.auth import AuthConfigError, issue_token
from app.core.config import Settings, get_settings
from app.core.errors import NotFound, ServiceUnavailable
from app.db.models import Tenant
from app.schemas.auth import DemoTenant, DemoTenantList, DemoTokenRequest, DemoTokenResponse

logger = logging.getLogger(__name__)

DEMO_LOGIN_ENV = "DEMO_LOGIN"
_OFF_VALUES = frozenset({"false", "0", "no", "off"})
# Span attribute for the slug; the id goes in `tracing.TENANT_ID` like on other routes.
TENANT_NAME_ATTR = "tenant.name"


def demo_login_enabled() -> bool:
    """`DEMO_LOGIN`, default on. Only an explicit false/0/no/off (any case) turns it off."""
    value = os.environ.get(DEMO_LOGIN_ENV, "").strip().lower()
    return value not in _OFF_VALUES


@dataclass(frozen=True)
class DirectoryTenant:
    id: uuid.UUID
    name: str
    label: str


class TenantDirectory(Protocol):
    def list(self) -> list[DirectoryTenant]: ...

    def get(self, name: str) -> DirectoryTenant | None: ...


def _entry(tenant: Tenant) -> DirectoryTenant:
    return DirectoryTenant(id=tenant.id, name=tenant.name, label=tenant.display_name or tenant.name)


@dataclass(frozen=True)
class DbTenantDirectory:
    """Reads `tenants` with the app role. Only name and display name ever leave it."""

    factory: sessionmaker[Session]

    def list(self) -> list[DirectoryTenant]:
        with self.factory() as session:
            tenants = session.scalars(select(Tenant).order_by(Tenant.name)).all()
        return [_entry(tenant) for tenant in tenants]

    def get(self, name: str) -> DirectoryTenant | None:
        with self.factory() as session:
            tenant = session.scalar(select(Tenant).where(Tenant.name == name))
        return _entry(tenant) if tenant is not None else None


def get_tenant_directory(
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> TenantDirectory:
    return DbTenantDirectory(factory)


Directory = Annotated[TenantDirectory, Depends(get_tenant_directory)]

router = APIRouter(prefix="/auth/demo", tags=["auth (demo)"])


@router.get("/tenants", response_model=DemoTenantList)
def list_demo_tenants(directory: Directory) -> DemoTenantList:
    """The tenants a demo token can be issued for. Demo mode only (`DEMO_LOGIN`)."""
    return DemoTenantList(
        tenants=[DemoTenant(name=t.name, label=t.label) for t in directory.list()]
    )


@router.post(
    "/token",
    response_model=DemoTokenResponse,
    responses={404: {"description": "Unknown tenant (Problem Details)"}},
)
def issue_demo_token(
    body: DemoTokenRequest,
    directory: Directory,
    settings: Annotated[Settings, Depends(get_settings)],
) -> DemoTokenResponse:
    """A token for the tenant, the same one `make token` issues. Demo mode only: this is
    not authentication."""
    tenant = directory.get(body.tenant)
    if tenant is None:
        logger.info("auth.demo_token_rejected", extra={"reason": "unknown_tenant"})
        raise NotFound("Unknown tenant.")

    tracing.set_attributes(**{tracing.TENANT_ID: str(tenant.id), TENANT_NAME_ATTR: tenant.name})
    try:
        token = issue_token(settings, tenant.id, tenant.name)
    except AuthConfigError:
        logger.exception("auth.key_unavailable")
        raise ServiceUnavailable("Authentication is not configured.") from None

    # The tenant only: never the token.
    logger.info(
        "auth.demo_token_issued",
        extra={"tenant_name": tenant.name, "tenant_id": str(tenant.id)},
    )
    return DemoTokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_ttl_hours * 3600,
        tenant=DemoTenant(name=tenant.name, label=tenant.label),
    )
