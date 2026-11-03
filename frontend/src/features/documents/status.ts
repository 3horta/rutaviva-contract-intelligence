import type { DocumentStatus, FailureCode } from "../../api/types";

/** Colour families shared by every badge of the app (see Badge.module.css). */
export type Tone = "neutral" | "pending" | "processing" | "success" | "warning" | "failed";

export interface StatusMeta {
  label: string;
  tone: Tone;
  /** Shown as help text next to totals. */
  description: string;
  terminal: boolean;
}

/** The single source of truth for how a document status looks and behaves. */
export const DOCUMENT_STATUS: Readonly<Record<DocumentStatus, StatusMeta>> = {
  pending: {
    label: "Pending",
    tone: "pending",
    description: "Waiting for a worker",
    terminal: false,
  },
  processing: {
    label: "Processing",
    tone: "processing",
    description: "Extracting and indexing",
    terminal: false,
  },
  ready: {
    label: "Ready",
    tone: "success",
    description: "Searchable in questions",
    terminal: true,
  },
  failed: {
    label: "Failed",
    tone: "failed",
    description: "Could not be processed",
    terminal: true,
  },
};

/** Display order: the life of a document. */
export const DOCUMENT_STATUSES: readonly DocumentStatus[] = [
  "pending",
  "processing",
  "ready",
  "failed",
];

/** A terminal document will not change by itself: no reason to keep polling it. */
export function isTerminal(status: DocumentStatus): boolean {
  return DOCUMENT_STATUS[status].terminal;
}

export function statusMeta(status: DocumentStatus): StatusMeta {
  return DOCUMENT_STATUS[status];
}

/** Human explanation of each failure code; `failure_detail` adds the specifics. */
export const FAILURE_REASON: Readonly<Record<FailureCode, string>> = {
  corrupt_pdf: "The file could not be read as a PDF. It may be damaged or password-protected.",
  empty_pdf: "The PDF has no extractable text. It is probably a scanned image.",
  embedding_unavailable:
    "The embedding service was unavailable. Upload the file again in a few minutes.",
  timeout: "Processing took too long and was stopped.",
  worker_lost: "The worker processing the document stopped unexpectedly.",
  internal_error: "An unexpected error occurred while processing the document.",
};

export function failureReason(code: FailureCode | null): string {
  return code ? FAILURE_REASON[code] : "The document could not be processed.";
}
