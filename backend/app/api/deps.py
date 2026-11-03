"""FastAPI dependencies shared by the routers. (Being implemented.)

Contract (routers and tests rely on these names and signatures):
- `get_current_tenant` -> `TenantContext`: verifies the `Authorization: Bearer <JWT>`
  header (signature, `exp`, `iss`) and that the tenant exists; raises `Unauthorized`.
  The tenant comes ONLY from the verified token, never from the request.
- `get_tenant_session` -> `Session`: a session inside `tenant_transaction` for the current
  tenant (RLS applies); commits when the request handler returns, rolls back on error.
- `get_storage` -> `Storage`, `get_dispatcher` -> `TaskDispatcher`: overridable in tests.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ports import Storage, TaskDispatcher


@dataclass(frozen=True)
class TenantContext:
    id: uuid.UUID
    name: str


def get_current_tenant() -> TenantContext:
    raise NotImplementedError


def get_tenant_session() -> Iterator[Session]:
    raise NotImplementedError


def get_storage() -> Storage:
    raise NotImplementedError


def get_dispatcher() -> TaskDispatcher:
    raise NotImplementedError
