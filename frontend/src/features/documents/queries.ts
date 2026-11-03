import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  getChunk,
  getDocument,
  getDocumentFile,
  getDocumentStats,
  listDocuments,
} from "../../api/documents";
import type { DocumentListQuery } from "../../api/types";
import { isTerminal } from "./status";

/** List polling while a visible document is still moving. */
export const LIST_POLL_MS = 3_000;
/** Detail polling until the document reaches a terminal status. */
export const DETAIL_POLL_MS = 2_000;

export const documentKeys = {
  all: ["documents"] as const,
  list: (query: DocumentListQuery) => ["documents", "list", query] as const,
  stats: () => ["documents", "stats"] as const,
  detail: (id: string) => ["documents", "detail", id] as const,
  file: (id: string) => ["documents", "file", id] as const,
  chunk: (id: string, chunkId: string) => ["documents", "chunk", id, chunkId] as const,
};

export function useDocumentList(query: DocumentListQuery) {
  return useQuery({
    queryKey: documentKeys.list(query),
    queryFn: ({ signal }) => listDocuments(query, signal),
    // Keeps the current page on screen while the next one loads.
    placeholderData: keepPreviousData,
    refetchInterval: (q) =>
      q.state.data?.results.some((doc) => !isTerminal(doc.status)) ? LIST_POLL_MS : false,
  });
}

export function useDocumentStats() {
  return useQuery({
    queryKey: documentKeys.stats(),
    queryFn: ({ signal }) => getDocumentStats(signal),
    refetchInterval: (q) => {
      const counts = q.state.data?.by_status;
      return counts && counts.pending + counts.processing > 0 ? LIST_POLL_MS : false;
    },
  });
}

export function useDocument(id: string) {
  return useQuery({
    queryKey: documentKeys.detail(id),
    queryFn: ({ signal }) => getDocument(id, signal),
    refetchInterval: (q) => {
      const doc = q.state.data;
      // Nothing to poll until the first answer; a terminal document will not change.
      return doc && !isTerminal(doc.status) ? DETAIL_POLL_MS : false;
    },
  });
}

export function useDocumentFile(id: string, enabled: boolean) {
  return useQuery({
    queryKey: documentKeys.file(id),
    queryFn: ({ signal }) => getDocumentFile(id, signal),
    enabled,
    // The bytes of a stored PDF never change.
    staleTime: Infinity,
  });
}

export function useChunk(documentId: string, chunkId: string | null) {
  return useQuery({
    queryKey: documentKeys.chunk(documentId, chunkId ?? ""),
    queryFn: ({ signal }) => getChunk(documentId, chunkId ?? "", signal),
    enabled: chunkId !== null,
    staleTime: Infinity,
  });
}
