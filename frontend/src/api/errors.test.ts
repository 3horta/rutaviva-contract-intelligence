import { ApiError, apiErrorFromBody, toApiError } from "./errors";

describe("apiErrorFromBody", () => {
  it("reads RFC 9457 Problem Details", () => {
    const body = JSON.stringify({
      type: "https://rutaviva.dev/problems/file-too-large",
      title: "Payload too large",
      status: 413,
      detail: "The file is larger than the 20 MB limit.",
      trace_id: "4bf92f3577b34da6a3ce929d0e0e4736",
    });
    const error = apiErrorFromBody(413, body);

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(413);
    expect(error.title).toBe("Payload too large");
    expect(error.detail).toBe("The file is larger than the 20 MB limit.");
    expect(error.message).toBe(error.detail);
    expect(error.type).toBe("https://rutaviva.dev/problems/file-too-large");
    expect(error.traceId).toBe("4bf92f3577b34da6a3ce929d0e0e4736");
  });

  it("falls back to the title when there is no detail", () => {
    const error = apiErrorFromBody(404, JSON.stringify({ title: "Not found", status: 404 }));
    expect(error.detail).toBe("Not found");
    expect(error.traceId).toBeNull();
    expect(error.isNotFound).toBe(true);
  });

  it("flags 401 as unauthorized", () => {
    expect(apiErrorFromBody(401, "").isUnauthorized).toBe(true);
  });

  it.each([
    ["an HTML error page", "<html><body>502 Bad Gateway</body></html>"],
    ["an empty body", ""],
    ["a JSON array", "[1, 2]"],
  ])("gives a sensible message for %s", (_, body) => {
    const error = apiErrorFromBody(502, body);
    expect(error.status).toBe(502);
    expect(error.detail).toBe("The server is not reachable right now.");
  });

  it("ignores a non-string detail (such as a validation list)", () => {
    const error = apiErrorFromBody(422, JSON.stringify({ detail: [{ loc: ["query", "limit"] }] }));
    expect(error.detail).toBe("The request was not valid.");
  });

  it("uses a generic message for unknown statuses", () => {
    expect(apiErrorFromBody(418, "").detail).toBe("The request failed (HTTP 418).");
  });
});

describe("toApiError", () => {
  it("keeps an ApiError as it is", () => {
    const error = apiErrorFromBody(500, "");
    expect(toApiError(error)).toBe(error);
  });

  it("wraps anything else", () => {
    const error = toApiError(new TypeError("boom"));
    expect(error).toBeInstanceOf(ApiError);
    expect(error.detail).toBe("boom");
    expect(error.status).toBe(0);
  });
});
