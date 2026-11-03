"""POST /query end to end: real Postgres, fake embeddings and fake LLM.

Authentication is another module: `get_current_tenant` and `get_tenant_session` are
overridden with a test tenant and a `tenant_transaction` session for it.
"""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.config import Settings
from app.db.session import tenant_transaction
from app.main import create_app
from app.schemas.query import INSUFFICIENT_EVIDENCE_MESSAGE
from tests.integration.test_retrieval import PM_A, SHARED, add_document


@pytest.fixture
def client_for(app_sessions: sessionmaker[Session], two_tenants):
    """Index one document per tenant and return a factory of clients acting as a tenant."""
    tenant_a, tenant_b = two_tenants
    with tenant_transaction(app_sessions, tenant_a) as session:
        maintenance = add_document(session, tenant_a, "maintenance.pdf", [PM_A, SHARED])
    with tenant_transaction(app_sessions, tenant_b) as session:
        add_document(session, tenant_b, "carrier.pdf", [SHARED])

    def make(tenant_id: uuid.UUID) -> TestClient:
        app = create_app(Settings(_env_file=None))

        def session_override() -> Iterator[Session]:
            with tenant_transaction(app_sessions, tenant_id) as session:
                yield session

        app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
            id=tenant_id, name=f"test-{tenant_id}"
        )
        app.dependency_overrides[get_tenant_session] = session_override
        return TestClient(app)

    return make, tenant_a, maintenance.id


def test_answer_with_citations(client_for) -> None:
    make, tenant_a, maintenance_id = client_for
    response = make(tenant_a).post(
        "/query",
        json={"question": "How often must the Supplier perform a PM-A service on each tractor?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert body["conversation_id"] is None
    assert "25,000 miles" in body["answer"]
    assert body["warnings"] == []
    citation = body["citations"][0]
    assert citation["document_id"] == str(maintenance_id)
    assert citation["document_filename"] == "maintenance.pdf"
    assert citation["page"] == 1
    assert citation["quote"] in PM_A


def test_shared_paragraph_is_cited_from_the_own_tenant(client_for) -> None:
    make, tenant_a, maintenance_id = client_for
    response = make(tenant_a).post(
        "/query", json={"question": "What does Incident Reporting Protocol RV-IRP-7 require?"}
    )
    assert response.status_code == 200
    citations = response.json()["citations"]
    assert citations
    assert {c["document_id"] for c in citations} == {str(maintenance_id)}


def test_question_with_no_matching_words_is_insufficient_evidence(client_for) -> None:
    make, tenant_a, _ = client_for
    response = make(tenant_a).post("/query", json={"question": "zebra quantum giraffe?"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "insufficient_evidence"
    assert body["answer"] == INSUFFICIENT_EVIDENCE_MESSAGE
    assert body["confidence"] == "low"
    assert body["citations"] == []


def test_empty_question_is_rejected(client_for) -> None:
    make, tenant_a, _ = client_for
    assert make(tenant_a).post("/query", json={"question": ""}).status_code == 422
