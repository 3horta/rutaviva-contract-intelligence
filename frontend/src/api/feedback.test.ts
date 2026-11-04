// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { setFetchImplementation } from "./client";
import { sendFeedback, setFeedbackRouteAvailable } from "./feedback";

afterEach(() => setFeedbackRouteAvailable(false));

describe("sendFeedback", () => {
  it("does not call the API while it has no feedback route", async () => {
    const fetch = vi.fn();
    setFetchImplementation(fetch);
    setFeedbackRouteAvailable(false);
    await expect(sendFeedback("c1", "m1", { rating: "down", comment: "  Wrong clause " })).resolves.toEqual({
      rating: "down",
      comment: "Wrong clause",
    });
    await expect(sendFeedback("c1", "m1", null)).resolves.toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("PUTs a rating and DELETEs to clear it", async () => {
    const fetch = vi.fn((_input: string, init?: RequestInit) =>
      Promise.resolve(
        init?.method === "DELETE"
          ? new Response(null, { status: 204 })
          : new Response(JSON.stringify({ rating: "up", comment: null }), { status: 200 }),
      ),
    );
    setFetchImplementation(fetch);
    setFeedbackRouteAvailable(true);

    await expect(sendFeedback("c1", "m1", { rating: "up" })).resolves.toEqual({ rating: "up", comment: null });
    expect(fetch).toHaveBeenLastCalledWith(
      "/api/conversations/c1/messages/m1/feedback",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ rating: "up" }) }),
    );
    await expect(sendFeedback("c1", "m1", null)).resolves.toBeNull();
    expect(fetch).toHaveBeenLastCalledWith(
      "/api/conversations/c1/messages/m1/feedback",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
