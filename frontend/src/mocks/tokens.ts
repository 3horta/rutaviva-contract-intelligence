/**
 * Unsigned demo tokens for mock mode only. Same claims the real API puts in
 * its JWTs (`make token tenant=...`); the mock backend reads `tenant_id`.
 */
export const DEMO_TENANTS = [
  { tenant_id: "tenant_a", tenant_name: "Rutaviva Logistics (Laredo)" },
  { tenant_id: "tenant_b", tenant_name: "Rutaviva Cross-Border (Nuevo Laredo)" },
] as const;

const base64Url = (value: object) =>
  btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export const DEMO_TOKEN_TTL_SECONDS = 8 * 3600;

export function demoToken(tenant: (typeof DEMO_TENANTS)[number]): string {
  const now = Math.floor(Date.now() / 1000);
  return [
    base64Url({ alg: "none", typ: "JWT" }),
    base64Url({ sub: "demo-user", ...tenant, iat: now, exp: now + DEMO_TOKEN_TTL_SECONDS }),
    "demo-signature",
  ].join(".");
}

export function demoTokens(): string[] {
  return DEMO_TENANTS.map(demoToken);
}
