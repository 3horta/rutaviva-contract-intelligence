/**
 * Types of the backend contract. Written by hand for now; to be replaced by
 * the output of openapi-typescript once the OpenAPI schema is stable.
 */

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export type FailureCode =
  | "corrupt_pdf"
  | "empty_pdf"
  | "embedding_unavailable"
  | "timeout"
  | "worker_lost"
  | "internal_error";

export interface DocumentMetrics {
  processing_ms: number;
  embedding_calls: number;
  embedding_tokens: number;
  /** True when the provider did not report usage and the count was estimated. */
  tokens_estimated: boolean;
}

export interface DocumentOut {
  id: string;
  filename: string;
  status: DocumentStatus;
  failure_code: FailureCode | null;
  failure_detail: string | null;
  page_count: number | null;
  chunk_count: number | null;
  embedding_model: string | null;
  metrics: DocumentMetrics | null;
  created_at: string;
  updated_at: string;
}

export const DOCUMENT_ORDERINGS = [
  "created_at",
  "-created_at",
  "filename",
  "-filename",
  "status",
  "-status",
] as const;

export type DocumentOrdering = (typeof DOCUMENT_ORDERINGS)[number];

export interface DocumentListQuery {
  limit: number;
  offset: number;
  ordering: DocumentOrdering;
  search?: string;
  status?: DocumentStatus;
}

export interface DocumentList {
  count: number;
  limit: number;
  offset: number;
  results: DocumentOut[];
}

export interface DocumentStats {
  total: number;
  by_status: Record<DocumentStatus, number>;
}

export interface UploadResponse {
  document_id: string;
  status: DocumentStatus;
  duplicate: boolean;
}

/** [x0, top, x1, bottom] in PDF points, origin at the top-left of the page. */
export type BBox = [number, number, number, number];

export interface ChunkOut {
  id: string;
  document_id: string;
  page: number;
  chunk_index: number;
  content: string;
  bbox: BBox | null;
  /** Size in PDF points of the page the bbox was measured on; null if unknown. */
  page_width: number | null;
  page_height: number | null;
}

/** RFC 9457 Problem Details, as every error of the API is returned. */
export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail?: string;
  trace_id?: string;
}
