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

/* Questions and conversations */

/** "contextualizing" only comes on follow-ups, before "searching". */
export type QueryPhase = "contextualizing" | "searching" | "drafting" | "validating";
export type AnswerStatus = "answered" | "insufficient_evidence";
export type Confidence = "low" | "medium" | "high";
/** Which search found a chunk: semantic, keyword, or both. */
export type FoundBy = "vector" | "full_text" | "both";
export type InsufficientReason = "no_results" | "llm_declared" | "validation_failed";

/** The fixed text of an answer the documents do not support. */
export const INSUFFICIENT_EVIDENCE_ANSWER =
  "There is not enough information in your documents to answer this question.";

export interface QueryRequest {
  question: string;
  /** Omitted to start a new conversation. */
  conversation_id?: string;
  /** Asks the last question of the conversation again, replacing its last turn. */
  retry?: boolean;
}

export interface Citation {
  chunk_id: string;
  document_id: string;
  document_filename: string;
  page: number;
  quote: string;
}

export interface RetrievedChunk {
  document_id: string;
  document_filename: string;
  page: number;
  chunk_id: string;
  score: number;
  found_by: FoundBy;
}

/** How an answer was produced: shown under "How it was answered". */
export interface AnswerDetails {
  /** A follow-up rewritten to stand on its own, when the context was used. */
  standalone_question: string | null;
  uses_context: boolean;
  retrieved: RetrievedChunk[];
  vector_hits: number;
  full_text_hits: number;
  attempts: number;
  insufficient_reason: InsufficientReason | null;
  latency_ms: number;
  llm_model: string;
  trace_id: string | null;
}

export interface QueryResponse {
  conversation_id: string | null;
  /** The stored assistant message; null only when the answer was not persisted. */
  message_id: string | null;
  answer: string;
  status: AnswerStatus;
  confidence: Confidence;
  citations: Citation[];
  warnings: string[];
  details: AnswerDetails | null;
}

/** First event of a stream: the conversation the answer belongs to. */
export interface ConversationStarted {
  conversation_id: string;
  title: string;
}

export interface PhaseUpdate {
  phase: QueryPhase;
  label: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationListQuery {
  limit: number;
  offset: number;
  search?: string;
}

export interface ConversationList {
  count: number;
  limit: number;
  offset: number;
  results: ConversationSummary[];
}

/** A stored message. Answer-only fields are null (or empty) on a question. */
export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: AnswerStatus | null;
  confidence: Confidence | null;
  citations: Citation[];
  warnings: string[];
  details: AnswerDetails | null;
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: ConversationMessage[];
}
