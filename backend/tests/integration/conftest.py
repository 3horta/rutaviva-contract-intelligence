"""Tests against the real Compose Postgres, with the migrations already applied
(`alembic upgrade head`).

Locally they are skipped if the database does not respond. With REQUIRE_DB=1 (set in
CI) they fail instead: a green CI run must mean the isolation tests actually ran."""

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, delete, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import Tenant
from app.db.session import make_engine, make_session_factory


@pytest.fixture(scope="session")
def db_settings() -> Settings:
    return Settings(_env_file=None)


def _skip_or_fail(reason: str) -> None:
    if os.getenv("REQUIRE_DB") == "1":
        pytest.fail(f"REQUIRE_DB=1 but {reason}")
    pytest.skip(reason)


def _connect_or_skip(url: str) -> Engine:
    engine = make_engine(url)
    try:
        with engine.connect():
            pass
    except OperationalError:
        _skip_or_fail("Postgres unavailable: docker compose up -d db && alembic upgrade head")
    return engine


@pytest.fixture(scope="session")
def owner_engine(db_settings: Settings) -> Iterator[Engine]:
    """Owner role (superuser): bypasses RLS. Only for setting up and cleaning data."""
    engine = _connect_or_skip(db_settings.migrations_database_url)
    with engine.connect() as conn:
        # Without migrations the app role does not even exist: skip with the reason.
        if conn.scalar(text("SELECT to_regclass('alembic_version')")) is None:
            _skip_or_fail("Database not migrated: alembic upgrade head")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_sessions(db_settings: Settings, owner_engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Sessions with the app role, the same one the API and the worker use."""
    engine = _connect_or_skip(db_settings.database_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def two_tenants(owner_engine: Engine) -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    """Two new tenants; deleted with all their data afterwards."""
    ids = (uuid.uuid4(), uuid.uuid4())
    with Session(owner_engine) as session, session.begin():
        for tenant_id in ids:
            session.add(Tenant(id=tenant_id, name=f"test-{tenant_id}"))
    yield ids
    with owner_engine.begin() as conn:
        for table in ("message_feedback", "messages", "conversations", "chunks", "documents"):
            conn.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = ANY(:ids)"), {"ids": list(ids)}
            )
        conn.execute(delete(Tenant).where(Tenant.id.in_(ids)))
