"""Engine y sesiones. La dependencia de FastAPI llega en T2.2; aquí solo lo común
a la API, el worker y los tests."""

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
    """Fija el inquilino de la transacción en curso para las políticas RLS.

    Equivale a `SET LOCAL app.tenant_id = ...`, pero `SET` no admite parámetros:
    `set_config(..., true)` sí, y el `true` lo limita a la transacción. Al volver
    la conexión al pool no queda ningún inquilino fijado.
    """
    session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


@contextmanager
def tenant_transaction(factory: sessionmaker[Session], tenant_id: uuid.UUID) -> Iterator[Session]:
    """Una transacción con el inquilino fijado; commit al salir, rollback si falla."""
    with factory() as session, session.begin():
        set_tenant(session, tenant_id)
        yield session
