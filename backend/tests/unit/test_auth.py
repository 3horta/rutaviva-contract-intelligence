import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest

from app.core.auth import AuthConfigError, issue_token, load_signing_key, verify_token
from app.core.config import Settings

KEY = "k" * 40


def _settings(**overrides) -> Settings:
    values = {"jwt_secret": KEY, "jwt_secret_file": Path("/nonexistent/jwt_secret")}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_issued_token_verifies_and_carries_the_tenant() -> None:
    settings = _settings()
    tenant_id = uuid.uuid4()

    claims = verify_token(settings, issue_token(settings, tenant_id, "tenant_a"))

    assert claims.tenant_id == tenant_id
    assert claims.tenant_name == "tenant_a"


def test_token_has_the_agreed_claims_and_ttl() -> None:
    settings = _settings(jwt_ttl_hours=2)
    now = datetime.now(UTC).replace(microsecond=0)
    tenant_id = uuid.uuid4()

    token = issue_token(settings, tenant_id, "tenant_a", now=now)

    payload = jwt.decode(token, KEY, algorithms=["HS256"], issuer="rutaviva")
    assert set(payload) == {"sub", "tenant_id", "tenant_name", "iss", "iat", "exp"}
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["exp"] - payload["iat"] == 2 * 3600


def test_key_is_read_from_the_file_when_no_env_key(tmp_path: Path) -> None:
    key_file = tmp_path / "jwt_secret"
    key_file.write_text(f"  {KEY}\n", encoding="utf-8")

    assert load_signing_key(_settings(jwt_secret=None, jwt_secret_file=key_file)) == KEY


def test_env_key_wins_over_the_file(tmp_path: Path) -> None:
    key_file = tmp_path / "jwt_secret"
    key_file.write_text("f" * 40, encoding="utf-8")

    assert load_signing_key(_settings(jwt_secret_file=key_file)) == KEY


def test_missing_key_is_a_clear_configuration_error() -> None:
    with pytest.raises(AuthConfigError, match="JWT_SECRET"):
        load_signing_key(_settings(jwt_secret=None))


def test_short_key_is_rejected_without_printing_it() -> None:
    with pytest.raises(AuthConfigError) as excinfo:
        load_signing_key(_settings(jwt_secret="too-short-key"))
    assert "too-short-key" not in str(excinfo.value)


def test_expired_token_is_rejected() -> None:
    settings = _settings(jwt_ttl_hours=1)
    token = issue_token(
        settings, uuid.uuid4(), "tenant_a", now=datetime.now(UTC) - timedelta(hours=2)
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        verify_token(settings, token)


def test_wrong_issuer_is_rejected() -> None:
    token = issue_token(_settings(jwt_issuer="someone-else"), uuid.uuid4(), "tenant_a")

    with pytest.raises(jwt.InvalidIssuerError):
        verify_token(_settings(), token)


def test_token_signed_with_another_key_is_rejected() -> None:
    token = issue_token(_settings(jwt_secret="x" * 40), uuid.uuid4(), "tenant_a")

    with pytest.raises(jwt.InvalidSignatureError):
        verify_token(_settings(), token)


def test_unsigned_token_is_rejected() -> None:
    now = datetime.now(UTC)
    claims = {
        "sub": "x",
        "tenant_id": str(uuid.uuid4()),
        "tenant_name": "tenant_a",
        "iss": "rutaviva",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    token = jwt.encode(claims, key=None, algorithm="none")

    with pytest.raises(jwt.InvalidTokenError):
        verify_token(_settings(), token)


def test_token_without_a_required_claim_is_rejected() -> None:
    now = datetime.now(UTC)
    claims = {"sub": "x", "iss": "rutaviva", "iat": now, "exp": now + timedelta(hours=1)}
    token = jwt.encode(claims, KEY, algorithm="HS256")

    with pytest.raises(jwt.MissingRequiredClaimError):
        verify_token(_settings(), token)


def test_tenant_id_must_be_a_uuid() -> None:
    now = datetime.now(UTC)
    claims = {
        "sub": "x",
        "tenant_id": "not-a-uuid",
        "tenant_name": "tenant_a",
        "iss": "rutaviva",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    token = jwt.encode(claims, KEY, algorithm="HS256")

    with pytest.raises(jwt.InvalidTokenError):
        verify_token(_settings(), token)
