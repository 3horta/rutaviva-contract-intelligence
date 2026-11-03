"""Alembic: aplica las migraciones con el rol dueño de las tablas.

La URL sale de `Settings.migrations_database_url`, no de alembic.ini: una sola fuente
de configuración, y sin credenciales en archivos versionados.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.db import models  # noqa: F401  (registra las tablas en Base.metadata)
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_online() -> None:
    engine = create_engine(get_settings().migrations_database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Solo se admite el modo online: alembic upgrade head")
run_migrations_online()
