import { useQuery } from "@tanstack/react-query";
import { apiJson } from "../api/client";
import { ApiError } from "../api/errors";
import type { DemoTenant, DemoTenantList, DemoTokenResponse } from "../api/types";
import { authStore, type TenantSession } from "./authStore";

/**
 * Demo sign-in (ADR-016): the API lists its tenants and signs a token for the
 * one clicked. The routes exist only while the API runs with DEMO_LOGIN on, so
 * a 404 from the listing is how the UI learns that demo mode is off.
 */
export const DEMO_TENANTS_QUERY_KEY = ["auth", "demo-tenants"] as const;

/** The tenants, or null when demo mode is off. */
export async function fetchDemoTenants(): Promise<DemoTenant[] | null> {
  try {
    const body = await apiJson<DemoTenantList>("/auth/demo/tenants", {}, { anonymous: true });
    return body.tenants;
  } catch (error) {
    if (error instanceof ApiError && error.isNotFound) return null;
    throw error;
  }
}

/**
 * `data`: the tenants when demo mode is on, null when it is off, undefined
 * while loading or when the API could not be reached (the token form is shown).
 */
export function useDemoTenants() {
  return useQuery({
    queryKey: DEMO_TENANTS_QUERY_KEY,
    queryFn: fetchDemoTenants,
    // The API's mode does not change while it runs.
    staleTime: Infinity,
  });
}

/** Gets a token for the tenant and stores it exactly like a pasted one. */
export async function signInAsDemoTenant(tenant: string): Promise<TenantSession> {
  const body = await apiJson<DemoTokenResponse>(
    "/auth/demo/token",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tenant }),
    },
    { anonymous: true },
  );
  const result = authStore.addToken(body.access_token, { label: body.tenant.label });
  if (!result.ok) throw new Error(result.error);
  return result.session;
}
