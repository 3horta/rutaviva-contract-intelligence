import { apiJson } from "./client";
import { RATER_HEADER, raterId } from "./rater";
import type { FeedbackInput, FeedbackResult } from "./types";

const path = (conversationId: string, messageId: string) =>
  `/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/feedback`;

/**
 * Rates an answer as this browser's rater (`feedback`), replacing its earlier
 * rating, or clears it (`null`). Resolves with what is stored for this rater
 * (null once cleared) and the answer's totals.
 */
export function sendFeedback(
  conversationId: string,
  messageId: string,
  feedback: FeedbackInput | null,
): Promise<FeedbackResult> {
  const headers = { [RATER_HEADER]: raterId() };
  if (!feedback) {
    return apiJson<FeedbackResult>(path(conversationId, messageId), { method: "DELETE", headers });
  }
  return apiJson<FeedbackResult>(path(conversationId, messageId), {
    method: "PUT",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify(feedback),
  });
}
