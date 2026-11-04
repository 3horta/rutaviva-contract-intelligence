"""message_feedback: a user's rating of an answer

One row per assistant message: `up` or `down` and an optional comment (trimmed, at most
1000 characters). A composite FK `(message_id, tenant_id)` keeps it in the message's
tenant (it needs a unique `(id, tenant_id)` on `messages`) and deletes it with the
message. Same isolation as the other tenant tables: forced RLS with the tenant policy,
and read/write for the app role.

Downgrade drops the table (and its ratings) and the unique constraint it needed.

Revision ID: a8e41c7d92b3
Revises: 5d2e8a9c1b7f
Create Date: 2026-11-04 18:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

revision: str = "a8e41c7d92b3"
down_revision: str | Sequence[str] | None = "5d2e8a9c1b7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same predicate as the initial migration: with no tenant set, no row is visible.
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _app_role() -> str:
    from app.core.config import get_settings

    url = make_url(get_settings().database_url)
    if not url.username:
        raise RuntimeError("DATABASE_URL must include the app role's user")
    return url.username


def _grant(role: str) -> None:
    # GRANT takes no parameters: quote the role with format(%I), as the initial migration.
    conn = op.get_bind()
    statement = conn.execute(
        sa.text(
            "SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON message_feedback TO %I', "
            "CAST(:role AS text))"
        ),
        {"role": role},
    ).scalar_one()
    conn.exec_driver_sql(statement)


def upgrade() -> None:
    op.create_unique_constraint(op.f("uq_messages_id_tenant_id"), "messages", ["id", "tenant_id"])
    op.create_table(
        "message_feedback",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rating IN ('up', 'down')", name=op.f("ck_message_feedback_rating")),
        sa.CheckConstraint(
            "comment IS NULL OR char_length(comment) BETWEEN 1 AND 1000",
            name=op.f("ck_message_feedback_comment_length"),
        ),
        sa.ForeignKeyConstraint(
            ["message_id", "tenant_id"],
            ["messages.id", "messages.tenant_id"],
            name=op.f("fk_message_feedback_message_id_tenant_id_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_message_feedback")),
    )
    op.create_index(
        op.f("ix_message_feedback_tenant_id"), "message_feedback", ["tenant_id"], unique=False
    )

    _grant(_app_role())
    op.execute("ALTER TABLE message_feedback ENABLE ROW LEVEL SECURITY")
    # FORCE: the policy also applies to the table owner.
    op.execute("ALTER TABLE message_feedback FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON message_feedback "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )


def downgrade() -> None:
    # The policy and the grants go with the table.
    op.drop_index(op.f("ix_message_feedback_tenant_id"), table_name="message_feedback")
    op.drop_table("message_feedback")
    op.drop_constraint(op.f("uq_messages_id_tenant_id"), "messages", type_="unique")
