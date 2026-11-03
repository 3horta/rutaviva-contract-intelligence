import type { ProblemDetails } from "./types";

/** The one error type the UI handles, whatever went wrong with a request. */
export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string;
  readonly type: string;
  readonly traceId: string | null;

  constructor(problem: {
    status: number;
    title: string;
    detail?: string | null;
    type?: string;
    traceId?: string | null;
  }) {
    const detail = problem.detail || problem.title;
    super(detail);
    this.name = "ApiError";
    this.status = problem.status;
    this.title = problem.title;
    this.detail = detail;
    this.type = problem.type ?? "about:blank";
    this.traceId = problem.traceId ?? null;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

/** Used when the body is not Problem Details (a proxy page, an empty body). */
const FALLBACK_TITLES: Record<number, string> = {
  400: "The request was not valid.",
  401: "Your access token is invalid or has expired.",
  403: "You do not have access to this resource.",
  404: "Not found.",
  413: "The file is too large.",
  415: "Only PDF files are accepted.",
  422: "The request was not valid.",
  429: "Too many requests. Try again in a moment.",
  502: "The server is not reachable right now.",
  503: "The service is temporarily unavailable.",
  504: "The server took too long to answer.",
};

function isProblem(value: unknown): value is Partial<ProblemDetails> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const asString = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim() ? value : undefined;

/**
 * Builds an ApiError from a failed response's status and body text. Pure, so
 * it is tested without a network. Tolerates bodies that are not Problem
 * Details: the error is always usable, with a sensible message.
 */
export function apiErrorFromBody(status: number, bodyText: string): ApiError {
  let body: unknown = null;
  try {
    body = bodyText ? JSON.parse(bodyText) : null;
  } catch {
    body = null;
  }

  const fallback = FALLBACK_TITLES[status] ?? `The request failed (HTTP ${status}).`;
  if (!isProblem(body)) {
    return new ApiError({ status, title: fallback });
  }

  return new ApiError({
    status: typeof body.status === "number" ? body.status : status,
    title: asString(body.title) ?? fallback,
    detail: asString(body.detail),
    type: asString(body.type),
    traceId: asString(body.trace_id),
  });
}

export function networkError(): ApiError {
  return new ApiError({
    status: 0,
    title: "Could not reach the server. Check your connection and try again.",
  });
}

/** For anything thrown that is not an ApiError (a bug, a parsing error). */
export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  return new ApiError({
    status: 0,
    title: error instanceof Error ? error.message : "Something went wrong.",
  });
}
