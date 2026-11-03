"""Test JWTs that carry the tenant (HS256, symmetric key).

The API only trusts the tenant written in a token it signed itself. The signing key never
lives in the repo: it comes from `JWT_SECRET` or, if that is unset, from the one-line file
at `JWT_SECRET_FILE` (generated on first start by the `migrate` service).

Claims: `sub` (the tenant id, as the token acts on behalf of the tenant), `tenant_id`,
`tenant_name`, `iss`, `iat`, `exp`.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings

ALGORITHM = "HS256"
REQUIRED_CLAIMS = ["sub", "tenant_id", "tenant_name", "iss", "iat", "exp"]
# RFC 7518 §3.2: an HS256 key must be at least as long as the hash output (32 bytes).
MIN_KEY_BYTES = 32


class AuthConfigError(RuntimeError):
    """The signing key is missing or unusable. A server problem, not the client's."""


@dataclass(frozen=True)
class TokenClaims:
    tenant_id: uuid.UUID
    tenant_name: str


def load_signing_key(settings: Settings) -> str:
    """Return the HS256 key: `jwt_secret` first, else the content of `jwt_secret_file`.

    Read on every call (a few bytes): the file may be created after the process starts,
    and rotating it does not need a restart.
    """
    if settings.jwt_secret is not None and settings.jwt_secret.get_secret_value().strip():
        key = settings.jwt_secret.get_secret_value().strip()
        source = "JWT_SECRET"
    else:
        path = settings.jwt_secret_file
        try:
            key = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise AuthConfigError(
                f"No JWT signing key: set JWT_SECRET or create the file {path}"
            ) from exc
        source = f"JWT_SECRET_FILE ({path})"

    if len(key.encode("utf-8")) < MIN_KEY_BYTES:
        # Never print the key itself, only where it came from.
        raise AuthConfigError(f"The JWT signing key from {source} is shorter than 32 bytes")
    return key


def issue_token(
    settings: Settings,
    tenant_id: uuid.UUID,
    tenant_name: str,
    *,
    now: datetime | None = None,
) -> str:
    """Sign a token for the tenant, valid for `jwt_ttl_hours`. `now` exists for tests."""
    issued_at = now or datetime.now(UTC)
    claims = {
        "sub": str(tenant_id),
        "tenant_id": str(tenant_id),
        "tenant_name": tenant_name,
        "iss": settings.jwt_issuer,
        "iat": issued_at,
        "exp": issued_at + timedelta(hours=settings.jwt_ttl_hours),
    }
    return jwt.encode(claims, load_signing_key(settings), algorithm=ALGORITHM)


def verify_token(settings: Settings, token: str) -> TokenClaims:
    """Check signature, `exp`, `iss` and the required claims.

    Raises `jwt.InvalidTokenError` (or a subclass) for any invalid token and
    `AuthConfigError` if the server has no usable key.
    """
    payload = jwt.decode(
        token,
        load_signing_key(settings),
        # Pinned: the algorithm in the token header is never trusted (no `none`, no RS/HS
        # confusion).
        algorithms=[ALGORITHM],
        issuer=settings.jwt_issuer,
        options={"require": REQUIRED_CLAIMS},
    )
    try:
        tenant_id = uuid.UUID(str(payload["tenant_id"]))
    except ValueError as exc:
        raise jwt.InvalidTokenError("tenant_id is not a UUID") from exc
    tenant_name = payload["tenant_name"]
    if not isinstance(tenant_name, str) or not tenant_name:
        raise jwt.InvalidTokenError("tenant_name is not a non-empty string")
    return TokenClaims(tenant_id=tenant_id, tenant_name=tenant_name)
