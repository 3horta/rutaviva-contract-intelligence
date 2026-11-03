"""Create (or update) the two demo tenants. Idempotent: safe to run on every start.

Uses the owner role (`MIGRATIONS_DATABASE_URL`): the app role can only read `tenants`.

    python scripts/seed.py
"""

import logging

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.models import Tenant

logger = logging.getLogger("scripts.seed")

# Slug (used in tokens and sample data) -> name shown in the UI.
TENANTS = {
    "tenant_a": "Fleet Procurement",
    "tenant_b": "Freight & Risk Procurement",
}


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    rows = [{"name": name, "display_name": display} for name, display in TENANTS.items()]
    # Upsert on the unique slug: a new tenant is inserted, an existing one keeps its id
    # (documents point to it) and only gets its display name refreshed.
    statement = insert(Tenant).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[Tenant.name],
        set_={"display_name": statement.excluded.display_name},
    )

    engine = create_engine(settings.migrations_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(statement)
    finally:
        engine.dispose()
    logger.info("seed.tenants_upserted", extra={"tenants": sorted(TENANTS)})


if __name__ == "__main__":
    main()
