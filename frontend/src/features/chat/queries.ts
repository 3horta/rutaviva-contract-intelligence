import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
} from "../../api/conversations";
import type { ConversationDetail } from "../../api/types";

/** Conversations per page of the sidebar ("Load more" asks for the next one). */
export const CONVERSATIONS_PAGE = 20;

export const conversationKeys = {
  all: ["conversations"] as const,
  lists: () => ["conversations", "list"] as const,
  list: (search: string) => ["conversations", "list", search] as const,
  detail: (id: string) => ["conversations", "detail", id] as const,
};

export function useConversationList(search: string) {
  return useInfiniteQuery({
    queryKey: conversationKeys.list(search),
    queryFn: ({ pageParam, signal }) =>
      listConversations({ limit: CONVERSATIONS_PAGE, offset: pageParam, search: search || undefined }, signal),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.results.length;
      return next < last.count ? next : undefined;
    },
  });
}

/** `enabled: false` keeps a conversation that is not stored yet from being fetched. */
export function useConversation(id: string | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: conversationKeys.detail(id ?? ""),
    queryFn: ({ signal }) => getConversation(id ?? "", signal),
    enabled: Boolean(id) && enabled,
  });
}

export function useRenameConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => renameConversation(id, title),
    onSuccess: (summary) => {
      queryClient.setQueryData<ConversationDetail>(conversationKeys.detail(summary.id), (detail) =>
        detail ? { ...detail, title: summary.title } : detail,
      );
      void queryClient.invalidateQueries({ queryKey: conversationKeys.lists() });
    },
  });
}

export function useDeleteConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onSuccess: (_, id) => {
      queryClient.removeQueries({ queryKey: conversationKeys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: conversationKeys.lists() });
    },
  });
}
