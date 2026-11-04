/**
 * Types of the backend contract, derived from `schema.d.ts`, which
 * openapi-typescript generates from the API's OpenAPI document
 * (`npm run gen:types`; see the Frontend section of the README). The names the
 * app uses stay here, so a schema change shows up as a type error where it matters.
 *
 * Hand-written only what the OpenAPI document cannot describe: the events of
 * the SSE stream, Problem Details, and a few refinements (the bbox tuple).
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

/* Demo sign-in (only while the API runs with DEMO_LOGIN on) */

export type DemoTenant = Schemas["DemoTenant"];
export type DemoTenantList = Schemas["DemoTenantList"];
export type DemoTokenResponse = Schemas["DemoTokenResponse"];

/* Documents */

export type DocumentOut = Schemas["DocumentOut"];
export type DocumentStatus = DocumentOut["status"];
export type FailureCode = NonNullable<DocumentOut["failure_code"]>;
export type DocumentMetrics = Schemas["DocumentMetrics"];
export type DocumentList = Schemas["DocumentList"];
export type DocumentStats = Schemas["DocumentStats"];
export type UploadResponse = Schemas["UploadResponse"];

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

/** [x0, top, x1, bottom] in PDF points, origin at the top-left of the page. */
export type BBox = [number, number, number, number];

/** The schema says `number[]`; the API always sends four numbers. */
export type ChunkOut = Omit<Schemas["ChunkOut"], "bbox"> & { bbox: BBox | null };

/** RFC 9457 Problem Details, as every error of the API is returned. */
export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail?: string;
  trace_id?: string;
}

/* Questions and conversations */

export type QueryRequest = Schemas["QueryRequest"];
export type QueryResponse = Schemas["QueryResponse"];
export type AnswerStatus = QueryResponse["status"];
export type Confidence = QueryResponse["confidence"];
export type Citation = Schemas["Citation"];
export type RetrievedChunk = Schemas["RetrievedChunkOut"];
export type FoundBy = RetrievedChunk["found_by"];
/** How an answer was produced: shown under "How it was answered". */
export type AnswerDetails = Schemas["AnswerDetails"];
export type InsufficientReason = NonNullable<AnswerDetails["insufficient_reason"]>;

export type ConversationSummary = Schemas["ConversationSummary"];
export type ConversationList = Schemas["ConversationList"];
/** A stored message. Answer-only fields are null (or empty) on a question. */
export type ConversationMessage = Schemas["MessageOut"];
/**
 * A stored answer's status. "interrupted": the answer was stopped before the
 * server saved it, so only the question was kept (the content is empty).
 */
export type MessageStatus = NonNullable<ConversationMessage["status"]>;
export type ConversationDetail = Schemas["ConversationOut"];

/**
 * A user's rating of an answer. Hand-written until the API stores feedback
 * (then it comes from the schema): `comment` is trimmed, null when empty.
 */
export type FeedbackRating = "up" | "down";
export interface MessageFeedback {
  rating: FeedbackRating;
  comment: string | null;
}
/** Body of PUT .../messages/{id}/feedback. */
export interface FeedbackInput {
  rating: FeedbackRating;
  comment?: string | null;
}
/** At most this many characters of comment (the API rejects more). */
export const FEEDBACK_COMMENT_MAX = 1_000;

export interface ConversationListQuery {
  limit: number;
  offset: number;
  search?: string;
}

/** The fixed text of an answer the documents do not support. */
export const INSUFFICIENT_EVIDENCE_ANSWER =
  "There is not enough information in your documents to answer this question.";

/* Events of POST /query/stream (not described by the OpenAPI document). */

/** "contextualizing" only comes on follow-ups, before "searching". */
export type QueryPhase = "contextualizing" | "searching" | "drafting" | "validating";

/** First event of a stream: the conversation the answer belongs to. */
export interface ConversationStarted {
  conversation_id: string;
  title: string;
}

export interface PhaseUpdate {
  phase: QueryPhase;
  label: string;
}

/** After `result` (real LLM only): follow-up questions for that answer, already stored. */
export interface SuggestionsUpdate {
  message_id: string;
  suggestions: string[];
}

/** After `result` (real LLM only): a new conversation's generated title, already stored. */
export interface TitleUpdate {
  conversation_id: string;
  title: string;
}
