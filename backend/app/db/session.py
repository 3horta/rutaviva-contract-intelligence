"""Engine and sessions. The FastAPI dependency is not here yet; only what the API,
the worker and the tests share."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def set_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    """Set the tenant of the current transaction for the RLS policies.

    Equivalent to `SET LOCAL app.tenant_id = ...`, but `SET` does not accept parameters:
    `set_config(..., true)` does, and `true` scopes it to the transaction. When the
    connection returns to the pool, no tenant remains set.
    """
    session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


@contextmanager
def tenant_transaction(factory: sessionmaker[Session], tenant_id: uuid.UUID) -> Iterator[Session]:
    """A transaction with the tenant set; commits on exit, rolls back on error."""
    with factory() as session, session.begin():
        set_tenant(session, tenant_id)
        yield session
