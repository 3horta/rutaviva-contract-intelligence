"""Conversation endpoints: list, read, rename and delete. Turns are added by `/query`.

A conversation of another tenant answers 404, never 403, so its existence is not revealed.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import CurrentTenant, TenantSession
from app.conversations import service
from app.schemas.conversations import (
    ConversationList,
    ConversationOut,
    ConversationRename,
    ConversationSummary,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=ConversationList)
def list_conversations(
    tenant: CurrentTenant,
    session: TenantSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> ConversationList:
    """The tenant's conversations, most recent activity first. `search` matches the title
    (case-insensitive)."""
    return service.list_conversations(session, tenant.id, limit=limit, offset=offset, search=search)


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation_id: uuid.UUID, tenant: CurrentTenant, session: TenantSession
) -> ConversationOut:
    """The conversation with all its messages, in order."""
    return service.get_conversation(session, tenant.id, conversation_id)


@router.patch("/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(
    conversation_id: uuid.UUID,
    body: ConversationRename,
    tenant: CurrentTenant,
    session: TenantSession,
) -> ConversationSummary:
    return service.rename_conversation(session, tenant.id, conversation_id, body.title)


@router.delete("/{conversation_id}", status_code=204, response_class=Response)
def delete_conversation(
    conversation_id: uuid.UUID, tenant: CurrentTenant, session: TenantSession
) -> Response:
    """Delete the conversation and all its messages."""
    service.delete_conversation(session, tenant.id, conversation_id)
    return Response(status_code=204)
