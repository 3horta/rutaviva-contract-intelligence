"""Demo sign-in (ADR-016): routes, the DEMO_LOGIN switch and the issued token.

The tenant lookup is replaced by an in-memory directory; the token check of
`get_current_tenant` runs for real against a fake session factory.
"""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.demo_auth import DirectoryTenant, demo_login_enabled, get_tenant_directory
from app.api.deps import CurrentTenant, get_session_factory
from app.core.auth import verify_token
from app.core.config import get_settings
from app.db.models import Tenant
from app.main import create_app

TENANT_A = DirectoryTenant(id=uuid.uuid4(), name="tenant_a", label="Fleet Procurement")
TENANT_B = DirectoryTenant(id=uuid.uuid4(), name="tenant_b", label="Freight & Risk Procurement")


class FakeDirectory:
    def __init__(self, *tenants: DirectoryTenant) -> None:
        self.tenants = {tenant.name: tenant for tenant in tenants}

    def list(self) -> list[DirectoryTenant]:
        return sorted(self.tenants.values(), key=lambda t: t.name)

    def get(self, name: str) -> DirectoryTenant | None:
        return self.tenants.get(name)


class FakeSession:
    """Answers `get_current_tenant`'s lookup by id from a dict."""

    def __init__(self, tenants: dict[uuid.UUID, Tenant]) -> None:
        self.tenants = tenants

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def scalar(self, statement: Any) -> Tenant | None:
        for value in statement.compile().params.values():
            if value in self.tenants:
                return self.tenants[value]
        return None


def _app(make_settings, monkeypatch, demo_login: str | None = None) -> FastAPI:
    if demo_login is not None:
        monkeypatch.setenv("DEMO_LOGIN", demo_login)
    app = create_app(make_settings(JWT_TTL_HOURS="2"))
    app.dependency_overrides[get_tenant_directory] = lambda: FakeDirectory(TENANT_A, TENANT_B)
    rows = {t.id: Tenant(id=t.id, name=t.name, display_name=t.label) for t in (TENANT_A, TENANT_B)}
    app.dependency_overrides[get_session_factory] = lambda: lambda: FakeSession(rows)

    # A route that only authenticates, to show the issued token is accepted.
    @app.get("/test-whoami")
    def whoami(tenant: CurrentTenant) -> dict[str, str]:
        return {"id": str(tenant.id), "name": tenant.name}

    return app


def _assert_problem(response, status: int) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert {"type", "title", "status", "detail", "trace_id"} <= set(body)
    return body


@pytest.mark.parametrize("value", [None, "", "true", "1", "yes", "TRUE", "anything"])
def test_demo_login_is_on_unless_explicitly_off(monkeypatch, value) -> None:
    if value is None:
        monkeypatch.delenv("DEMO_LOGIN", raising=False)
    else:
        monkeypatch.setenv("DEMO_LOGIN", value)
    assert demo_login_enabled()


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "False", " NO ", "OFF"])
def test_demo_login_can_be_turned_off(monkeypatch, value) -> None:
    monkeypatch.setenv("DEMO_LOGIN", value)
    assert not demo_login_enabled()


def test_lists_tenants_with_their_labels_only(make_settings, monkeypatch) -> None:
    client = TestClient(_app(make_settings, monkeypatch))

    response = client.get("/auth/demo/tenants")

    assert response.status_code == 200
    assert response.json() == {
        "tenants": [
            {"name": "tenant_a", "label": "Fleet Procurement"},
            {"name": "tenant_b", "label": "Freight & Risk Procurement"},
        ]
    }
    # No ids: the frontend only needs the name to ask for a token.
    assert str(TENANT_A.id) not in response.text


def test_issues_the_same_token_as_make_token(make_settings, monkeypatch) -> None:
    app = _app(make_settings, monkeypatch)
    client = TestClient(app)

    response = client.post("/auth/demo/token", json={"tenant": "tenant_a"})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 2 * 3600
    assert body["tenant"] == {"name": "tenant_a", "label": "Fleet Procurement"}
    settings = app.dependency_overrides[get_settings]()
    claims = verify_token(settings, body["access_token"])
    assert claims.tenant_id == TENANT_A.id
    assert claims.tenant_name == "tenant_a"


def test_issued_token_is_accepted_by_an_authenticated_route(make_settings, monkeypatch) -> None:
    client = TestClient(_app(make_settings, monkeypatch))
    token = client.post("/auth/demo/token", json={"tenant": "tenant_b"}).json()["access_token"]

    response = client.get("/test-whoami", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"id": str(TENANT_B.id), "name": "tenant_b"}


def test_unknown_tenant_is_404_problem_details(make_settings, monkeypatch) -> None:
    client = TestClient(_app(make_settings, monkeypatch))

    body = _assert_problem(client.post("/auth/demo/token", json={"tenant": "tenant_z"}), 404)

    assert body["type"] == "/problems/not-found"
    assert "tenant_z" not in body["detail"]


def test_empty_tenant_is_a_validation_problem(make_settings, monkeypatch) -> None:
    client = TestClient(_app(make_settings, monkeypatch))

    _assert_problem(client.post("/auth/demo/token", json={"tenant": "  "}), 422)


def test_issuing_logs_the_tenant_but_never_the_token(make_settings, monkeypatch, caplog) -> None:
    client = TestClient(_app(make_settings, monkeypatch))

    with caplog.at_level("INFO", logger="app.api.demo_auth"):
        token = client.post("/auth/demo/token", json={"tenant": "tenant_a"}).json()["access_token"]

    issued = [r for r in caplog.records if r.getMessage() == "auth.demo_token_issued"]
    assert len(issued) == 1
    assert issued[0].tenant_name == "tenant_a"
    assert all(token not in str(r.__dict__) for r in caplog.records)


def test_routes_are_in_openapi_by_default(make_settings, monkeypatch) -> None:
    paths = _app(make_settings, monkeypatch).openapi()["paths"]

    assert "/auth/demo/tenants" in paths
    assert "/auth/demo/token" in paths


def test_demo_login_off_removes_both_routes(make_settings, monkeypatch) -> None:
    app = _app(make_settings, monkeypatch, demo_login="false")
    client = TestClient(app)

    _assert_problem(client.get("/auth/demo/tenants"), 404)
    _assert_problem(client.post("/auth/demo/token", json={"tenant": "tenant_a"}), 404)
    assert not any(path.startswith("/auth/demo") for path in app.openapi()["paths"])
