import { apiArrayBuffer, apiJson } from "./client";
import type {
  ChunkOut,
  DocumentList,
  DocumentListQuery,
  DocumentOut,
  DocumentStats,
  UploadResponse,
} from "./types";

export function listDocuments(query: DocumentListQuery, signal?: AbortSignal): Promise<DocumentList> {
  const params = new URLSearchParams({
    limit: String(query.limit),
    offset: String(query.offset),
    ordering: query.ordering,
  });
  if (query.search) params.set("search", query.search);
  if (query.status) params.set("status", query.status);
  return apiJson<DocumentList>(`/documents?${params}`, { signal });
}

export function getDocumentStats(signal?: AbortSignal): Promise<DocumentStats> {
  return apiJson<DocumentStats>("/documents/stats", { signal });
}

export function getDocument(id: string, signal?: AbortSignal): Promise<DocumentOut> {
  return apiJson<DocumentOut>(`/documents/${encodeURIComponent(id)}`, { signal });
}

/** The original PDF. Fetched with the auth header, so it cannot be a plain link. */
export function getDocumentFile(id: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  return apiArrayBuffer(`/documents/${encodeURIComponent(id)}/file`, { signal });
}

export function getChunk(documentId: string, chunkId: string, signal?: AbortSignal): Promise<ChunkOut> {
  return apiJson<ChunkOut>(
    `/documents/${encodeURIComponent(documentId)}/chunks/${encodeURIComponent(chunkId)}`,
    { signal },
  );
}

export function uploadDocument(file: File, signal?: AbortSignal): Promise<UploadResponse> {
  const body = new FormData();
  body.append("file", file, file.name);
  return apiJson<UploadResponse>("/documents", { method: "POST", body, signal });
}
