"""messages.status: allow 'interrupted'

A streamed turn whose client disconnects (Stop) before the answer is saved keeps the
question, with an empty assistant message whose status is `interrupted`.

Downgrade deletes the interrupted turns (both messages of each one): the old constraint
does not accept them, and they hold no answer. RLS is forced on `messages`, so it is
lifted for the delete in case the migration role is the table owner but not a superuser.

Revision ID: 5d2e8a9c1b7f
Revises: f7cdb6f67209
Create Date: 2026-11-04 16:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "5d2e8a9c1b7f"
down_revision: str | Sequence[str] | None = "f7cdb6f67209"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "status IS NULL OR status IN ('answered', 'insufficient_evidence')"
_NEW = "status IS NULL OR status IN ('answered', 'insufficient_evidence', 'interrupted')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_messages_status"), "messages", type_="check")
    op.create_check_constraint("status", "messages", _NEW)


def downgrade() -> None:
    op.execute("ALTER TABLE messages NO FORCE ROW LEVEL SECURITY")
    # The question of an interrupted turn is the message right before its answer.
    op.execute(
        """
        DELETE FROM messages AS m
        USING messages AS a
        WHERE a.status = 'interrupted'
          AND m.conversation_id = a.conversation_id
          AND m.sequence IN (a.sequence, a.sequence - 1)
        """
    )
    op.execute("ALTER TABLE messages FORCE ROW LEVEL SECURITY")
    op.drop_constraint(op.f("ck_messages_status"), "messages", type_="check")
    op.create_check_constraint("status", "messages", _OLD)
