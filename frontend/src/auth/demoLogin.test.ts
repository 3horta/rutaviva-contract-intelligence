import { describe, expect, it } from "vitest";
import { apiErrorFromBody } from "../api/errors";
import { isApiStarting } from "./demoLogin";

describe("isApiStarting", () => {
  it("waits while the proxy answers that the API is not up yet", () => {
    for (const status of [502, 503, 504]) {
      expect(isApiStarting(apiErrorFromBody(status, ""))).toBe(true);
    }
  });

  it("waits when the request got no answer at all", () => {
    expect(isApiStarting(new TypeError("Failed to fetch"))).toBe(true);
  });

  it("does not wait on an answer from the API itself", () => {
    expect(isApiStarting(apiErrorFromBody(404, ""))).toBe(false);
    expect(isApiStarting(apiErrorFromBody(500, ""))).toBe(false);
    expect(isApiStarting(apiErrorFromBody(401, ""))).toBe(false);
  });
});
