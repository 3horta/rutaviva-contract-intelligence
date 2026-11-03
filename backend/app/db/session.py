"""Engine and sessions shared by the API, the worker and the tests.

Every read or write of tenant data goes through `tenant_transaction`, which sets the
tenant for RLS. A session that forgets to set it sees nothing (the policy fails closed).
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@lru_cache
def session_factory_for(url: str) -> sessionmaker[Session]:
    """One engine (and connection pool) per database URL and process."""
    return make_session_factory(make_engine(url))


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
