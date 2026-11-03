"""Documents API against the real Postgres, with the app role (RLS applies).

The file storage is a temporary directory and the dispatcher a recording double: no
Celery, no Redis.
"""

import hashlib
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.api import documents as documents_module
from app.api.deps import get_dispatcher, get_storage
from app.core.auth import AuthConfigError, issue_token
from app.core.config import Settings
from app.db.models import Chunk, Document
from app.db.session import tenant_transaction
from app.ingestion.storage import LocalDiskStorage
from app.main import create_app

KEY = "integration-test-signing-key-0123456789"


@dataclass
class RecordingDispatcher:
    """Records enqueued documents. On each call it opens a NEW session and checks the row
    is already visible: that proves the API enqueues only after committing."""

    sessions: sessionmaker[Session]
    calls: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)
    fail: bool = False

    def enqueue_document(self, document_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        with tenant_transaction(self.sessions, tenant_id) as session:
            status = session.scalar(select(Document.status).where(Document.id == document_id))
        assert status == "pending", "enqueued before the document was committed"
        self.calls.append((document_id, tenant_id))
        if self.fail:
            raise ConnectionError("broker unreachable")


@dataclass
class Api:
    client: TestClient
    settings: Settings
    dispatcher: RecordingDispatcher
    files_dir: Path
    tenant_a: uuid.UUID
    tenant_b: uuid.UUID

    def headers(self, tenant_id: uuid.UUID) -> dict[str, str]:
        token = issue_token(self.settings, tenant_id, f"test-{tenant_id}")
        return {"Authorization": f"Bearer {token}"}

    def upload(self, tenant_id: uuid.UUID, data: bytes, filename: str = "contract.pdf"):
        return self.client.post(
            "/documents",
            headers=self.headers(tenant_id),
            files={"file": (filename, data, "application/pdf")},
        )


@pytest.fixture
def api(
    tmp_path: Path,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
    app_sessions: sessionmaker[Session],
) -> Iterator[Api]:
    settings = Settings(
        _env_file=None,
        jwt_secret=KEY,
        jwt_secret_file=tmp_path / "missing",
        files_dir=tmp_path / "files",
        max_upload_mb=1,
    )
    app = create_app(settings)
    dispatcher = RecordingDispatcher(app_sessions)
    storage = LocalDiskStorage(settings.files_dir)
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_dispatcher] = lambda: dispatcher
    with TestClient(app) as client:
        yield Api(client, settings, dispatcher, settings.files_dir, *two_tenants)


def _pdf() -> bytes:
    """A different PDF every time, so no two tests share a sha256."""
    return b"%PDF-1.7\n" + os.urandom(32).hex().encode() + b"\n%%EOF\n"


def _insert_document(owner_engine: Engine, tenant_id: uuid.UUID, **values) -> uuid.UUID:
    # The id is chosen here: reading `document.id` after the session closes would need a
    # refresh of an expired, detached instance (the default expire_on_commit=True).
    document_id = values.pop("id", uuid.uuid4())
    document = Document(
        id=document_id,
        tenant_id=tenant_id,
        filename=values.pop("filename", "contract.pdf"),
        sha256=values.pop("sha256", hashlib.sha256(os.urandom(16)).hexdigest()),
        **values,
    )
    with Session(owner_engine) as session, session.begin():
        session.add(document)
    return document_id


def _assert_problem(response, status: int) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == status
    assert {"type", "title", "detail", "trace_id"} <= set(body)
    return body


# --- Authentication ---------------------------------------------------------------------


def test_missing_header_is_401(api: Api) -> None:
    body = _assert_problem(api.client.get("/documents"), 401)
    assert body["detail"] == "Missing or invalid credentials."


@pytest.mark.parametrize("value", ["Basic abc", "Bearer", "Bearer not.a.jwt", "token"])
def test_malformed_header_is_401(api: Api, value: str) -> None:
    _assert_problem(api.client.get("/documents", headers={"Authorization": value}), 401)


def test_bad_signature_is_401(api: Api) -> None:
    other = api.settings.model_copy(update={"jwt_secret": None})
    other_key = api.files_dir.parent / "other_key"
    other_key.write_text("another-signing-key-that-is-long-enough", encoding="utf-8")
    other = other.model_copy(update={"jwt_secret_file": other_key})
    token = issue_token(other, api.tenant_a, f"test-{api.tenant_a}")

    response = api.client.get("/documents", headers={"Authorization": f"Bearer {token}"})

    _assert_problem(response, 401)


def test_expired_token_is_401(api: Api) -> None:
    token = issue_token(
        api.settings,
        api.tenant_a,
        f"test-{api.tenant_a}",
        now=datetime.now(UTC) - timedelta(hours=api.settings.jwt_ttl_hours + 1),
    )

    response = api.client.get("/documents", headers={"Authorization": f"Bearer {token}"})

    _assert_problem(response, 401)


def test_wrong_issuer_is_401(api: Api) -> None:
    other = api.settings.model_copy(update={"jwt_issuer": "not-rutaviva"})
    token = issue_token(other, api.tenant_a, f"test-{api.tenant_a}")

    response = api.client.get("/documents", headers={"Authorization": f"Bearer {token}"})

    _assert_problem(response, 401)


def test_unknown_tenant_is_401(api: Api) -> None:
    ghost = uuid.uuid4()
    token = issue_token(api.settings, ghost, f"test-{ghost}")

    response = api.client.get("/documents", headers={"Authorization": f"Bearer {token}"})

    _assert_problem(response, 401)


def test_tenant_name_must_match(api: Api) -> None:
    token = issue_token(api.settings, api.tenant_a, "someone-else")

    response = api.client.get("/documents", headers={"Authorization": f"Bearer {token}"})

    _assert_problem(response, 401)


def test_api_does_not_start_without_a_signing_key(api: Api) -> None:
    # Fail fast, like invalid settings: no key means no request could be authenticated.
    broken = api.settings.model_copy(update={"jwt_secret": None})
    with pytest.raises(AuthConfigError):
        create_app(broken)


def test_key_lost_after_startup_is_503_not_401(api: Api, monkeypatch) -> None:
    # Safety net in the dependency: the key file disappears while the API is running.
    headers = api.headers(api.tenant_a)

    def key_is_gone(*_args, **_kwargs):
        raise AuthConfigError("gone")

    monkeypatch.setattr("app.api.deps.verify_token", key_is_gone)
    _assert_problem(api.client.get("/documents", headers=headers), 503)


# --- Upload -----------------------------------------------------------------------------


def test_upload_creates_a_pending_document_and_enqueues_it_once(
    api: Api, app_sessions: sessionmaker[Session]
) -> None:
    data = _pdf()

    response = api.upload(api.tenant_a, data, filename="../../etc/Tire Supply.pdf")

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["duplicate"] is False
    document_id = uuid.UUID(body["document_id"])
    assert api.dispatcher.calls == [(document_id, api.tenant_a)]

    with tenant_transaction(app_sessions, api.tenant_a) as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.status == "pending"
        assert document.filename == "Tire Supply.pdf"
        assert document.sha256 == hashlib.sha256(data).hexdigest()
        assert document.storage_key == f"{api.tenant_a}/{document_id}.pdf"
    assert (api.files_dir / document.storage_key).read_bytes() == data


def test_duplicate_upload_returns_the_existing_document(api: Api) -> None:
    data = _pdf()
    first = api.upload(api.tenant_a, data).json()

    response = api.upload(api.tenant_a, data, filename="renamed.pdf")

    assert response.status_code == 200
    assert response.json() == {
        "document_id": first["document_id"],
        "status": "pending",
        "duplicate": True,
    }
    assert len(api.dispatcher.calls) == 1


def test_same_pdf_in_another_tenant_is_not_a_duplicate(api: Api) -> None:
    data = _pdf()
    first = api.upload(api.tenant_a, data).json()

    response = api.upload(api.tenant_b, data)

    assert response.status_code == 202
    assert response.json()["document_id"] != first["document_id"]


def test_failed_document_can_be_uploaded_again(api: Api, owner_engine: Engine) -> None:
    data = _pdf()
    first = api.upload(api.tenant_a, data).json()
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE documents SET status = 'failed', failure_code = 'corrupt_pdf' "
                "WHERE id = :id"
            ),
            {"id": first["document_id"]},
        )

    response = api.upload(api.tenant_a, data)

    assert response.status_code == 202
    assert response.json()["document_id"] != first["document_id"]
    assert len(api.dispatcher.calls) == 2


def test_concurrent_duplicate_is_resolved_by_the_unique_index(
    api: Api, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _pdf()
    # Another request committed the same file after our duplicate check: simulate it by
    # inserting the row first and making the check miss once.
    winner = _insert_document(owner_engine, api.tenant_a, sha256=hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(documents_module, "_find_live_duplicate", _miss_once())

    response = api.upload(api.tenant_a, data)

    assert response.status_code == 200, response.text
    assert response.json()["document_id"] == str(winner)
    assert response.json()["duplicate"] is True
    assert api.dispatcher.calls == []
    # The file stored for the losing upload was removed.
    tenant_dir = api.files_dir / str(api.tenant_a)
    assert not tenant_dir.exists() or list(tenant_dir.iterdir()) == []


def _miss_once():
    original = documents_module._find_live_duplicate
    state = {"missed": False}

    def find(session, tenant_id, sha256):
        if not state["missed"]:
            state["missed"] = True
            return None
        return original(session, tenant_id, sha256)

    return find


def test_enqueue_failure_marks_the_document_failed_and_returns_503(
    api: Api, app_sessions: sessionmaker[Session]
) -> None:
    api.dispatcher.fail = True

    response = api.upload(api.tenant_a, _pdf())

    _assert_problem(response, 503)
    document_id = api.dispatcher.calls[0][0]
    with tenant_transaction(app_sessions, api.tenant_a) as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.status == "failed"
        assert document.failure_code == "internal_error"
        assert document.failure_detail == "could not enqueue processing"


def test_too_large_is_413(api: Api) -> None:
    data = b"%PDF-1.7\n" + b"0" * (1024 * 1024)

    _assert_problem(api.upload(api.tenant_a, data), 413)
    assert api.dispatcher.calls == []


def test_not_a_pdf_is_415_whatever_the_name_or_content_type(api: Api) -> None:
    _assert_problem(api.upload(api.tenant_a, b"hello, I am text", filename="fake.pdf"), 415)


def test_empty_file_is_422(api: Api) -> None:
    _assert_problem(api.upload(api.tenant_a, b""), 422)


def test_missing_file_field_is_a_validation_problem(api: Api) -> None:
    response = api.client.post("/documents", headers=api.headers(api.tenant_a))

    body = _assert_problem(response, 422)
    assert body["errors"][0]["field"] == "body.file"


# --- Tenant isolation -------------------------------------------------------------------


def test_other_tenant_gets_404_for_detail_file_and_chunk(api: Api, owner_engine: Engine) -> None:
    document_id = uuid.UUID(api.upload(api.tenant_a, _pdf()).json()["document_id"])
    chunk_id = uuid.uuid4()
    with Session(owner_engine) as session, session.begin():
        session.add(
            Chunk(
                id=chunk_id,
                document_id=document_id,
                tenant_id=api.tenant_a,
                page=1,
                chunk_index=0,
                content="Payment terms are net 30.",
                embedding=[0.0] * 768,
                bbox=[10.0, 20.0, 300.0, 60.0],
                page_width=612.0,
                page_height=792.0,
            )
        )
    paths = [
        f"/documents/{document_id}",
        f"/documents/{document_id}/file",
        f"/documents/{document_id}/chunks/{chunk_id}",
    ]

    for path in paths:
        assert api.client.get(path, headers=api.headers(api.tenant_a)).status_code == 200
        _assert_problem(api.client.get(path, headers=api.headers(api.tenant_b)), 404)

    chunk = api.client.get(paths[2], headers=api.headers(api.tenant_a)).json()
    assert chunk["content"] == "Payment terms are net 30."
    assert chunk["bbox"] == [10.0, 20.0, 300.0, 60.0]


def test_chunk_must_belong_to_the_document(api: Api, owner_engine: Engine) -> None:
    first = uuid.UUID(api.upload(api.tenant_a, _pdf()).json()["document_id"])
    second = uuid.UUID(api.upload(api.tenant_a, _pdf()).json()["document_id"])
    chunk_id = uuid.uuid4()
    with Session(owner_engine) as session, session.begin():
        session.add(
            Chunk(
                id=chunk_id,
                document_id=first,
                tenant_id=api.tenant_a,
                page=1,
                chunk_index=0,
                content="text",
                embedding=[0.0] * 768,
            )
        )

    response = api.client.get(
        f"/documents/{second}/chunks/{chunk_id}", headers=api.headers(api.tenant_a)
    )

    _assert_problem(response, 404)


# --- Reads ------------------------------------------------------------------------------


def test_file_is_served_inline_as_pdf(api: Api) -> None:
    data = _pdf()
    document_id = api.upload(api.tenant_a, data, filename="Contrato año 2026.pdf").json()[
        "document_id"
    ]

    response = api.client.get(f"/documents/{document_id}/file", headers=api.headers(api.tenant_a))

    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        'inline; filename="Contrato a_o 2026.pdf"; '
        "filename*=UTF-8''Contrato%20a%C3%B1o%202026.pdf"
    )


def test_detail_includes_metrics_only_when_set(api: Api, owner_engine: Engine) -> None:
    pending = _insert_document(owner_engine, api.tenant_a)
    ready = _insert_document(
        owner_engine,
        api.tenant_a,
        status="ready",
        page_count=3,
        chunk_count=12,
        embedding_model="fake-hash-v1@768",
        processing_ms=1500,
        embedding_calls=1,
        embedding_tokens=900,
        embedding_tokens_estimated=True,
    )
    headers = api.headers(api.tenant_a)

    assert api.client.get(f"/documents/{pending}", headers=headers).json()["metrics"] is None
    body = api.client.get(f"/documents/{ready}", headers=headers).json()
    assert body["metrics"] == {
        "processing_ms": 1500,
        "embedding_calls": 1,
        "embedding_tokens": 900,
        "tokens_estimated": True,
    }
    assert body["chunk_count"] == 12


@pytest.fixture
def listed(api: Api, owner_engine: Engine) -> dict[str, uuid.UUID]:
    """Four documents of tenant A with known names, statuses and ages; one of tenant B."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rows = {
        "alpha_fuel.pdf": ("ready", None, 0),
        "beta tires.pdf": ("pending", None, 1),
        "gamma-insurance.pdf": ("failed", "corrupt_pdf", 2),
        "alphaXfuel.pdf": ("ready", None, 3),
    }
    ids = {}
    for name, (status, code, days) in rows.items():
        ids[name] = _insert_document(
            owner_engine,
            api.tenant_a,
            filename=name,
            status=status,
            failure_code=code,
            created_at=base + timedelta(days=days),
        )
    _insert_document(owner_engine, api.tenant_b, filename="other-tenant.pdf")
    return ids


def _names(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [row["filename"] for row in response.json()["results"]]


def test_list_shows_only_own_documents_newest_first(api: Api, listed) -> None:
    response = api.client.get("/documents", headers=api.headers(api.tenant_a))

    assert _names(response) == [
        "alphaXfuel.pdf",
        "gamma-insurance.pdf",
        "beta tires.pdf",
        "alpha_fuel.pdf",
    ]
    assert response.json()["count"] == 4


def test_list_pagination(api: Api, listed) -> None:
    response = api.client.get(
        "/documents", params={"limit": 2, "offset": 1}, headers=api.headers(api.tenant_a)
    )

    assert _names(response) == ["gamma-insurance.pdf", "beta tires.pdf"]
    assert response.json()["count"] == 4
    assert response.json()["limit"] == 2
    assert response.json()["offset"] == 1


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_list_rejects_out_of_range_pagination(api: Api, params) -> None:
    response = api.client.get("/documents", params=params, headers=api.headers(api.tenant_a))

    _assert_problem(response, 422)


def test_list_ordering(api: Api, listed) -> None:
    headers = api.headers(api.tenant_a)

    by_name = api.client.get("/documents", params={"ordering": "filename"}, headers=headers)
    by_status = api.client.get("/documents", params={"ordering": "-status"}, headers=headers)

    names = _names(by_name)
    # How "alpha_fuel" and "alphaXfuel" compare depends on the database collation.
    assert set(names[:2]) == {"alpha_fuel.pdf", "alphaXfuel.pdf"}
    assert names[2:] == ["beta tires.pdf", "gamma-insurance.pdf"]
    assert [row["status"] for row in by_status.json()["results"]] == [
        "ready",
        "ready",
        "pending",
        "failed",
    ]
    _assert_problem(
        api.client.get("/documents", params={"ordering": "sha256"}, headers=headers), 422
    )


def test_list_search_is_case_insensitive_and_escapes_wildcards(api: Api, listed) -> None:
    headers = api.headers(api.tenant_a)

    assert _names(api.client.get("/documents", params={"search": "ALPHA"}, headers=headers)) == [
        "alphaXfuel.pdf",
        "alpha_fuel.pdf",
    ]
    # `_` is literal, not "any character": it does not match "alphaXfuel".
    assert _names(api.client.get("/documents", params={"search": "alpha_"}, headers=headers)) == [
        "alpha_fuel.pdf"
    ]
    assert _names(api.client.get("/documents", params={"search": "%"}, headers=headers)) == []


def test_list_status_filter(api: Api, listed) -> None:
    response = api.client.get(
        "/documents", params={"status": "failed"}, headers=api.headers(api.tenant_a)
    )

    assert _names(response) == ["gamma-insurance.pdf"]
    assert response.json()["results"][0]["failure_code"] == "corrupt_pdf"


def test_stats_count_only_own_documents(api: Api, listed) -> None:
    response = api.client.get("/documents/stats", headers=api.headers(api.tenant_a))

    assert response.status_code == 200
    assert response.json() == {
        "total": 4,
        "by_status": {"pending": 1, "processing": 0, "ready": 2, "failed": 1},
    }
