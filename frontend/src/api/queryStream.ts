/**
 * Client of `POST /query/stream` (Server-Sent Events). EventSource cannot
 * POST nor send the Authorization header, so the stream is read with fetch
 * and its `event:` / `data:` blocks are parsed here.
 */
import { apiRequest } from "./client";
import { ApiError, apiErrorFromBody, networkError } from "./errors";
import type {
  ConversationStarted,
  PhaseUpdate,
  ProblemDetails,
  QueryRequest,
  QueryResponse,
} from "./types";

export interface SseMessage {
  event: string;
  data: string;
}

export type QueryStreamEvent =
  | { type: "conversation"; data: ConversationStarted }
  | { type: "phase"; data: PhaseUpdate }
  | { type: "result"; data: QueryResponse }
  | { type: "error"; data: ProblemDetails };

/** One block of lines, as the SSE spec reads it. Comments (": ping") are skipped. */
function parseBlock(block: string): SseMessage | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  return data.length ? { event, data: data.join("\n") } : null;
}

/**
 * The complete messages in `buffer`, and the incomplete tail to keep for the
 * next chunk. Accepts \n, \r\n and \r line ends; a trailing \r is held back
 * in case its \n arrives in the next chunk.
 */
export function takeSseMessages(buffer: string): { messages: SseMessage[]; rest: string } {
  const end = buffer.endsWith("\r") ? buffer.length - 1 : buffer.length;
  const blocks = buffer.slice(0, end).replace(/\r\n?/g, "\n").split("\n\n");
  const rest = (blocks.pop() ?? "") + buffer.slice(end);
  const messages = blocks.map(parseBlock).filter((m): m is SseMessage => m !== null);
  return { messages, rest };
}

const EVENT_TYPES = new Set(["conversation", "phase", "result", "error"]);

/** Unknown event names are ignored, so the backend can add new ones safely. */
function toEvent(message: SseMessage): QueryStreamEvent | null {
  if (!EVENT_TYPES.has(message.event)) return null;
  try {
    return { type: message.event, data: JSON.parse(message.data) } as QueryStreamEvent;
  } catch {
    throw new ApiError({ status: 0, title: "The server sent an answer that could not be read." });
  }
}

export const INCOMPLETE_STREAM_MESSAGE = "The answer stopped before it finished. Try again.";

/**
 * Reads the stream until its last event: resolves with the `result`, rejects
 * with an ApiError for an `error` event or a stream that ends without either.
 */
export async function readQueryStream(
  body: ReadableStream<Uint8Array>,
  onEvent?: (event: QueryStreamEvent) => void,
): Promise<QueryResponse> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") throw error;
        throw networkError();
      }
      // At the end, a last block without its blank line still counts.
      buffer += chunk.done ? `${decoder.decode()}\n\n` : decoder.decode(chunk.value, { stream: true });
      const { messages, rest } = takeSseMessages(buffer);
      buffer = rest;

      for (const message of messages) {
        const event = toEvent(message);
        if (!event) continue;
        if (event.type === "error") {
          throw apiErrorFromBody(event.data.status || 500, message.data);
        }
        onEvent?.(event);
        if (event.type === "result") return event.data;
      }
      if (chunk.done) throw new ApiError({ status: 0, title: INCOMPLETE_STREAM_MESSAGE });
    }
  } finally {
    reader.cancel().catch(() => undefined);
  }
}

interface StreamOptions {
  signal?: AbortSignal;
  onEvent?: (event: QueryStreamEvent) => void;
}

/**
 * Asks a question and follows its answer. Errors before the stream starts
 * (401, 404, 422) arrive as Problem Details, like any other request. Aborting
 * the signal rejects with an AbortError (the Stop button).
 */
export async function streamQuery(
  request: QueryRequest,
  { signal, onEvent }: StreamOptions = {},
): Promise<QueryResponse> {
  const response = await apiRequest("/query/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.body) throw new ApiError({ status: 0, title: INCOMPLETE_STREAM_MESSAGE });
  return readQueryStream(response.body, onEvent);
}
