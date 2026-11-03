"""tenants without api keys; storage key, metrics and chunk bbox

- tenants: authentication moved to signed test JWTs, so `api_key_hash` goes away;
  `display_name` is shown in the UI.
- documents: `storage_key` of the stored PDF and per-document ingestion metrics.
- chunks: bounding box on the page and page size, to highlight a citation in the PDF.

Revision ID: 07ee20867813
Revises: 4f9292f742cd
Create Date: 2026-11-03 15:12:46.647179

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "07ee20867813"
down_revision: str | Sequence[str] | None = "4f9292f742cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("bbox", postgresql.JSONB(), nullable=True))
    op.add_column("chunks", sa.Column("page_width", sa.Double(), nullable=True))
    op.add_column("chunks", sa.Column("page_height", sa.Double(), nullable=True))
    op.create_check_constraint(
        "bbox_four_numbers",
        "chunks",
        "bbox IS NULL OR (jsonb_typeof(bbox) = 'array' AND jsonb_array_length(bbox) = 4)",
    )
    op.add_column("documents", sa.Column("storage_key", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("processing_ms", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("embedding_calls", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("embedding_tokens", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("embedding_tokens_estimated", sa.Boolean(), nullable=True))
    op.add_column(
        "tenants", sa.Column("display_name", sa.Text(), server_default="", nullable=False)
    )
    op.drop_constraint(op.f("uq_tenants_api_key_hash"), "tenants", type_="unique")
    op.drop_column("tenants", "api_key_hash")


def downgrade() -> None:
    # Nullable on the way back: existing tenants have no key to restore.
    op.add_column("tenants", sa.Column("api_key_hash", sa.String(length=64), nullable=True))
    op.create_unique_constraint(op.f("uq_tenants_api_key_hash"), "tenants", ["api_key_hash"])
    op.drop_column("tenants", "display_name")
    op.drop_column("documents", "embedding_tokens_estimated")
    op.drop_column("documents", "embedding_tokens")
    op.drop_column("documents", "embedding_calls")
    op.drop_column("documents", "processing_ms")
    op.drop_column("documents", "storage_key")
    op.drop_constraint(op.f("ck_chunks_bbox_four_numbers"), "chunks", type_="check")
    op.drop_column("chunks", "page_height")
    op.drop_column("chunks", "page_width")
    op.drop_column("chunks", "bbox")
