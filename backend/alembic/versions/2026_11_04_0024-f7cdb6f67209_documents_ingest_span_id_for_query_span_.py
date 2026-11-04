"""documents ingest span id for query span links

A span link needs a span id of the target trace, not only its trace id: the worker
stores the span id of its ingestion span next to the trace id it already stored.

Revision ID: f7cdb6f67209
Revises: b3c1f0d2a9e4
Create Date: 2026-11-04 00:24:13.584049

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7cdb6f67209"
down_revision: str | Sequence[str] | None = "b3c1f0d2a9e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("ingest_span_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "ingest_span_id")
