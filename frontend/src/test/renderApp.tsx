import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router-dom";
import { setFetchImplementation } from "../api/client";
import { clearOnTenantSwitch } from "../api/queryClient";
import { authStore } from "../auth/authStore";
import { installMockApi } from "../mocks/server";
import { demoTokens } from "../mocks/tokens";
import { routes as appRoutes } from "../routes";

type FetchFn = Parameters<typeof setFetchImplementation>[0];

interface RenderAppOptions {
  /** The URL the app starts at. */
  route?: string;
  /** With the demo tokens of both tenants (tenant_a active), or with none. */
  signedIn?: boolean;
  /** Replaces the mock API (to spy on it or to force an answer). */
  fetch?: FetchFn;
  /** Another route tree (e.g. one with a page that throws). */
  routes?: RouteObject[];
}

let unbindPrevious: (() => void) | null = null;

/** Removes every stored token, so each test starts signed out. */
export function signOutAll(): void {
  authStore.getState().sessions.forEach((session) => authStore.remove(session.tenantKey));
}

/**
 * The whole app (shell, routes, queries) over the in-memory mock API, with no
 * simulated latency, a fresh QueryClient without retries and a memory router.
 */
export function renderApp({ route = "/dashboard", signedIn = true, fetch, routes = appRoutes }: RenderAppOptions = {}) {
  installMockApi({ instant: true });
  if (fetch) setFetchImplementation(fetch);

  signOutAll();
  if (signedIn) {
    // Added in reverse, so the first one (tenant_a) ends up active.
    [...demoTokens()].reverse().forEach((token) => authStore.addToken(token));
  }

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  unbindPrevious?.();
  unbindPrevious = clearOnTenantSwitch(queryClient);

  const router = createMemoryRouter(routes, { initialEntries: [route] });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...view, router, queryClient };
}
