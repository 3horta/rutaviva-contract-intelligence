"""Tests contra el Postgres real de Compose, con las migraciones ya aplicadas
(`alembic upgrade head`). Si la base no responde, se saltan."""

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


def _connect_or_skip(url: str) -> Engine:
    engine = make_engine(url)
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.skip("Postgres no disponible: docker compose up -d db && alembic upgrade head")
    return engine


@pytest.fixture(scope="session")
def owner_engine(db_settings: Settings) -> Iterator[Engine]:
    """Rol dueño (superusuario): se salta RLS. Solo para preparar y limpiar datos."""
    engine = _connect_or_skip(db_settings.migrations_database_url)
    with engine.connect() as conn:
        # Sin migrar, el rol de la app ni siquiera existe: mejor saltar con el motivo.
        if conn.scalar(text("SELECT to_regclass('alembic_version')")) is None:
            pytest.skip("Base sin migrar: alembic upgrade head")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_sessions(db_settings: Settings, owner_engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Sesiones con el rol de la app, el mismo que usarán la API y el worker."""
    engine = _connect_or_skip(db_settings.database_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def two_tenants(owner_engine: Engine) -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    """Dos inquilinos nuevos; al terminar se borran con todo lo suyo."""
    ids = (uuid.uuid4(), uuid.uuid4())
    with Session(owner_engine) as session, session.begin():
        for tenant_id in ids:
            session.add(
                Tenant(id=tenant_id, name=f"test-{tenant_id}", api_key_hash=tenant_id.hex * 2)
            )
    yield ids
    with owner_engine.begin() as conn:
        for table in ("messages", "conversations", "chunks", "documents"):
            conn.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = ANY(:ids)"), {"ids": list(ids)}
            )
        conn.execute(delete(Tenant).where(Tenant.id.in_(ids)))
