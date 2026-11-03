import { authStore } from "../auth/authStore";
import { apiErrorFromBody, networkError } from "./errors";

/** Every call goes through the same origin; Vite or Nginx proxy it to the API. */
export const API_PREFIX = "/api";

type FetchFn = (input: string, init?: RequestInit) => Promise<Response>;

let fetchImpl: FetchFn = (input, init) => fetch(input, init);

/** Mock mode swaps the network for an in-memory backend (see src/mocks). */
export function setFetchImplementation(fn: FetchFn): void {
  fetchImpl = fn;
}

/**
 * Sends a request with the active tenant's token. Any non-2xx answer becomes
 * an ApiError; a 401 also flags the token, so the shell can ask for a new one.
 */
export async function apiRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const token = authStore.getActiveToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");

  let response: Response;
  try {
    response = await fetchImpl(`${API_PREFIX}${path}`, { ...init, headers });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw networkError();
  }

  if (!response.ok) {
    const error = apiErrorFromBody(response.status, await response.text().catch(() => ""));
    if (error.isUnauthorized && token) authStore.markInvalid(token);
    throw error;
  }
  return response;
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiRequest(path, init);
  return (await response.json()) as T;
}

export async function apiArrayBuffer(path: string, init?: RequestInit): Promise<ArrayBuffer> {
  const response = await apiRequest(path, {
    ...init,
    headers: { Accept: "application/pdf", ...init?.headers },
  });
  return response.arrayBuffer();
}
