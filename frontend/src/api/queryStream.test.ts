// @vitest-environment node
import { describe, expect, it } from "vitest";
import { ApiError } from "./errors";
import { INCOMPLETE_STREAM_MESSAGE, readQueryStream, takeSseMessages, type QueryStreamEvent } from "./queryStream";
import type { QueryResponse } from "./types";

const RESULT: QueryResponse = {
  conversation_id: "c1",
  message_id: "m1",
  answer: "The term is **45 days**.",
  status: "answered",
  confidence: "high",
  citations: [],
  warnings: [],
  details: null,
};

const sse = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

/** A body that delivers the given text pieces as separate chunks. */
function body(...pieces: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      pieces.forEach((piece) => controller.enqueue(encoder.encode(piece)));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const events: QueryStreamEvent[] = [];
  const result = await readQueryStream(stream, (event) => events.push(event));
  return { events, result };
}

describe("takeSseMessages", () => {
  it("keeps an incomplete block for the next chunk", () => {
    const { messages, rest } = takeSseMessages("event: phase\ndata: {}\n\nevent: res");
    expect(messages).toEqual([{ event: "phase", data: "{}" }]);
    expect(rest).toBe("event: res");
  });

  it("joins multi-line data and skips comments", () => {
    const { messages } = takeSseMessages(": ping\n\nevent: x\ndata: a\ndata: b\n\n");
    expect(messages).toEqual([{ event: "x", data: "a\nb" }]);
  });

  it("accepts CRLF line ends, even split between chunks", () => {
    const first = takeSseMessages("event: x\r\ndata: 1\r\n\r");
    expect(first.messages).toEqual([]);
    const second = takeSseMessages(`${first.rest}\n`);
    expect(second.messages).toEqual([{ event: "x", data: "1" }]);
  });
});

describe("readQueryStream", () => {
  it("parses events split in the middle of a block", async () => {
    const text = sse("conversation", { conversation_id: "c1", title: "Payment" }) + sse("result", RESULT);
    const { events, result } = await collect(body(text.slice(0, 17), text.slice(17, 60), text.slice(60)));
    expect(events.map((e) => e.type)).toEqual(["conversation", "result"]);
    expect(result).toEqual(RESULT);
  });

  it("parses several events in one chunk", async () => {
    const text =
      sse("conversation", { conversation_id: "c1", title: "t" }) +
      sse("phase", { phase: "searching", label: "Searching" }) +
      sse("phase", { phase: "drafting", label: "Drafting" }) +
      sse("result", RESULT);
    const { events } = await collect(body(text));
    expect(events.map((e) => e.type)).toEqual(["conversation", "phase", "phase", "result"]);
  });

  it("ignores unknown events", async () => {
    const { events } = await collect(body(sse("heartbeat", {}), sse("result", RESULT)));
    expect(events.map((e) => e.type)).toEqual(["result"]);
  });

  it("rejects a stream that ends without a result", async () => {
    const stream = body(sse("phase", { phase: "searching", label: "Searching" }));
    await expect(readQueryStream(stream)).rejects.toThrow(INCOMPLETE_STREAM_MESSAGE);
  });

  it("turns an error event into an ApiError with its Problem Details", async () => {
    const problem = { type: "about:blank", title: "LLM unavailable", status: 503, detail: "Try later.", trace_id: "abc" };
    const error = await readQueryStream(body(sse("error", problem))).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 503, detail: "Try later.", traceId: "abc" });
  });
});
