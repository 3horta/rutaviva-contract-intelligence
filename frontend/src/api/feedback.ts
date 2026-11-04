import { apiJson, apiRequest } from "./client";
import type { FeedbackInput, MessageFeedback } from "./types";

const path = (conversationId: string, messageId: string) =>
  `/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/feedback`;

/**
 * The API has no feedback route yet: until it does, the real client keeps the
 * rating on screen only (calling a route that does not exist would fail every
 * click). The mock API implements the route and turns this on (`installMockApi`).
 */
let feedbackRouteAvailable = false;

export function setFeedbackRouteAvailable(available: boolean): void {
  feedbackRouteAvailable = available;
}

/**
 * Rates an answer (`feedback`), replacing any earlier rating, or clears it
 * (`null`). Resolves with what is stored: the feedback, or null once cleared.
 */
export async function sendFeedback(
  conversationId: string,
  messageId: string,
  feedback: FeedbackInput | null,
): Promise<MessageFeedback | null> {
  if (!feedbackRouteAvailable) {
    return feedback && { rating: feedback.rating, comment: feedback.comment?.trim() || null };
  }
  if (!feedback) {
    await apiRequest(path(conversationId, messageId), { method: "DELETE" });
    return null;
  }
  return apiJson<MessageFeedback>(path(conversationId, messageId), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(feedback),
  });
}
