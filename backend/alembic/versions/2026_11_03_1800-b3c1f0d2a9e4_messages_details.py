"""messages.details: how an answer was produced (assistant messages only)

The retrieved chunks (ids, pages, scores, which search branch found them), hit counts,
attempts and why an answer was "insufficient evidence". Ids and numbers only, never
document content. Null for user messages and for rows written before this column.

Revision ID: b3c1f0d2a9e4
Revises: 07ee20867813
Create Date: 2026-11-03 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3c1f0d2a9e4"
down_revision: str | Sequence[str] | None = "07ee20867813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("details", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "details")
