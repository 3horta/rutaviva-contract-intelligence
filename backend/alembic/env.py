"""Alembic: applies the migrations with the role that owns the tables.

The URL comes from `Settings.migrations_database_url`, not from alembic.ini: a single
source of configuration, and no credentials in committed files.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.db import models  # noqa: F401  (registers the tables in Base.metadata)
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
    raise SystemExit("Only online mode is supported: alembic upgrade head")
run_migrations_online()
