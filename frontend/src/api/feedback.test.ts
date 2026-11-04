import { describe, expect, it, vi } from "vitest";
import { setFetchImplementation } from "./client";
import { sendFeedback } from "./feedback";
import { raterId } from "./rater";

describe("sendFeedback", () => {
  it("PUTs a rating and DELETEs to clear it, as this browser's rater", async () => {
    const result = (feedback: unknown) =>
      new Response(JSON.stringify({ feedback, totals: { up: 1, down: 0 } }), { status: 200 });
    const fetch = vi.fn((_input: string, init?: RequestInit) =>
      Promise.resolve(result(init?.method === "DELETE" ? null : { rating: "up", comment: null })),
    );
    setFetchImplementation(fetch);

    await expect(sendFeedback("c1", "m1", { rating: "up" })).resolves.toEqual({
      feedback: { rating: "up", comment: null },
      totals: { up: 1, down: 0 },
    });
    const [path, put] = fetch.mock.calls[0] ?? [];
    expect(path).toBe("/api/conversations/c1/messages/m1/feedback");
    expect(put).toMatchObject({ method: "PUT", body: JSON.stringify({ rating: "up" }) });
    expect(new Headers(put?.headers).get("X-Rater-Id")).toBe(raterId());

    await expect(sendFeedback("c1", "m1", null)).resolves.toMatchObject({ feedback: null });
    const [, del] = fetch.mock.calls[1] ?? [];
    expect(del?.method).toBe("DELETE");
    expect(new Headers(del?.headers).get("X-Rater-Id")).toBe(raterId());
  });
});
