import { apiJson, apiRequest } from "./client";
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

export function getConversation(id: string, signal?: AbortSignal): Promise<ConversationDetail> {
  return apiJson<ConversationDetail>(path(id), { signal });
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
