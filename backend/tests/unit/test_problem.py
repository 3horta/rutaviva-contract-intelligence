from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.problem import install_problem_handlers
from app.core.errors import NotFound, Unauthorized


def _client() -> TestClient:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/not-found")
    def not_found() -> None:
        raise NotFound("Document not found.")

    @app.get("/unauthorized")
    def unauthorized() -> None:
        raise Unauthorized("Missing or invalid credentials.")

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/items")
    def items(limit: int) -> dict[str, int]:
        return {"limit": limit}

    # The unhandled-error handler re-raises after responding; here we want the response.
    return TestClient(app, raise_server_exceptions=False)


def _assert_problem(response, status: int) -> dict:
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert {"type", "title", "status", "detail", "trace_id"} <= set(body)
    assert body["status"] == status
    return body


def test_app_error_becomes_problem_details() -> None:
    body = _assert_problem(_client().get("/not-found"), 404)

    assert body["type"] == "/problems/not-found"
    assert body["title"] == "Not found"
    assert body["detail"] == "Document not found."
    # No recorded trace in unit tests.
    assert body["trace_id"] is None


def test_unauthorized_announces_the_bearer_scheme() -> None:
    response = _client().get("/unauthorized")

    _assert_problem(response, 401)
    assert response.headers["www-authenticate"] == "Bearer"


def test_validation_error_lists_fields_without_input_values() -> None:
    response = _client().get("/items", params={"limit": "not-a-number"})

    body = _assert_problem(response, 422)
    assert body["errors"][0]["field"] == "query.limit"
    assert body["errors"][0]["type"] == "int_parsing"
    assert "not-a-number" not in response.text


def test_unknown_route_is_problem_details() -> None:
    body = _assert_problem(_client().get("/nope"), 404)

    assert body["title"] == "Not Found"


def test_unhandled_error_hides_internals() -> None:
    response = _client().get("/boom")

    body = _assert_problem(response, 500)
    assert body["detail"] == "An unexpected error occurred."
    assert "secret internal detail" not in response.text
