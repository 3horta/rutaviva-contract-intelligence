"""Demo sign-in against the real Postgres (ADR-016): the tenants come from the
`tenants` table read with the app role, and a demo token is subject to exactly the same
isolation as any other token (token-derived tenant, explicit filter, RLS)."""

import hashlib
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Document
from app.main import create_app

KEY = "integration-test-signing-key-0123456789"


@pytest.fixture
def client(tmp_path: Path, monkeypatch, two_tenants) -> Iterator[TestClient]:
    monkeypatch.delenv("DEMO_LOGIN", raising=False)
    settings = Settings(
        _env_file=None,
        jwt_secret=KEY,
        jwt_secret_file=tmp_path / "missing",
        files_dir=tmp_path / "files",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _insert_document(owner_engine: Engine, tenant_id: uuid.UUID, filename: str) -> uuid.UUID:
    document_id = uuid.uuid4()
    with Session(owner_engine) as session, session.begin():
        session.add(
            Document(
                id=document_id,
                tenant_id=tenant_id,
                filename=filename,
                sha256=hashlib.sha256(os.urandom(16)).hexdigest(),
            )
        )
    return document_id


def _token(client: TestClient, tenant_id: uuid.UUID) -> dict[str, str]:
    response = client.post("/auth/demo/token", json={"tenant": f"test-{tenant_id}"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_lists_the_tenants_from_the_database(client: TestClient, two_tenants) -> None:
    response = client.get("/auth/demo/tenants")

    assert response.status_code == 200
    listed = {tenant["name"]: tenant["label"] for tenant in response.json()["tenants"]}
    # No display name set: the label falls back to the slug.
    for tenant_id in two_tenants:
        assert listed[f"test-{tenant_id}"] == f"test-{tenant_id}"


def test_unknown_tenant_is_404(client: TestClient) -> None:
    response = client.post("/auth/demo/token", json={"tenant": f"test-{uuid.uuid4()}"})

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


def test_demo_token_only_sees_its_own_tenant(
    client: TestClient, two_tenants, owner_engine: Engine
) -> None:
    tenant_a, tenant_b = two_tenants
    own = _insert_document(owner_engine, tenant_a, "own.pdf")
    other = _insert_document(owner_engine, tenant_b, "other.pdf")
    headers = _token(client, tenant_a)

    listed = client.get("/documents", headers=headers)
    assert listed.status_code == 200
    assert [doc["id"] for doc in listed.json()["results"]] == [str(own)]

    # Another tenant's document does not exist for this token.
    assert client.get(f"/documents/{other}", headers=headers).status_code == 404
    assert client.get(f"/documents/{other}/file", headers=headers).status_code == 404
