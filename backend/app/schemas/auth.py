"""API models of the demo sign-in (ADR-016)."""

from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints


class DemoTenant(BaseModel):
    # The stable slug (`tenant_a`) and the name shown in the UI ("Fleet Procurement").
    name: str
    label: str


class DemoTenantList(BaseModel):
    tenants: list[DemoTenant]


class DemoTokenRequest(BaseModel):
    tenant: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class DemoTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    # Seconds until the token expires (`JWT_TTL_HOURS`).
    expires_in: int
    tenant: DemoTenant
