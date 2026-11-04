import { apiJson, apiRequest } from "./client";
import { RATER_HEADER, raterId } from "./rater";
import type {
  ConversationDetail,
  ConversationList,
  ConversationListQuery,
  ConversationSummary,
} from "./types";

const path = (id: string) => `/conversations/${encodeURIComponent(id)}`;

export function listConversations(
  query: ConversationListQuery,
  signal?: AbortSignal,
): Promise<ConversationList> {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.search) params.set("search", query.search);
  return apiJson<ConversationList>(`/conversations?${params}`, { signal });
}

/** With this browser's rater id, so each answer carries its own feedback (ADR-019). */
export function getConversation(id: string, signal?: AbortSignal): Promise<ConversationDetail> {
  return apiJson<ConversationDetail>(path(id), { signal, headers: { [RATER_HEADER]: raterId() } });
}

export function renameConversation(id: string, title: string): Promise<ConversationSummary> {
  return apiJson<ConversationSummary>(path(id), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export async function deleteConversation(id: string): Promise<void> {
  await apiRequest(path(id), { method: "DELETE" });
}
