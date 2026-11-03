import { toApiError } from "../../api/errors";
import type { UploadResponse } from "../../api/types";
import type { Tone } from "../documents/status";

export type UploadState = "queued" | "uploading" | "sent" | "duplicate" | "error";

export interface UploadItem {
  /** Position in the batch: stable key, also used to patch the item. */
  index: number;
  name: string;
  size: number;
  state: UploadState;
  /** Why it was skipped or failed, or a note on a duplicate. */
  detail?: string;
  /** The document created for it, or the one that already had this PDF. */
  documentId?: string;
}

/** Uploads in flight at once: keeps the API busy without flooding it. */
export const MAX_CONCURRENT_UPLOADS = 3;

/** Same limit as the API; checked here so the user knows before sending. */
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

export const UPLOAD_STATE: Readonly<Record<UploadState, { label: string; tone: Tone }>> = {
  queued: { label: "Queued", tone: "neutral" },
  uploading: { label: "Uploading…", tone: "processing" },
  sent: { label: "Sent", tone: "success" },
  duplicate: { label: "Duplicate", tone: "warning" },
  error: { label: "Error", tone: "failed" },
};

export function isSettled(state: UploadState): boolean {
  return state !== "queued" && state !== "uploading";
}

const isPdf = (file: File) =>
  file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");

/** Same name, size and modification time: the same file picked twice. */
const fileKey = (file: File) => `${file.name}|${file.size}|${file.lastModified}`;

/**
 * Checks done before anything is sent. A file that fails them is settled
 * right away and never reaches the API; the rest are queued.
 */
export function precheck(files: readonly File[]): UploadItem[] {
  const seen = new Set<string>();
  return files.map((file, index): UploadItem => {
    const base = { index, name: file.name, size: file.size };
    if (!isPdf(file)) return { ...base, state: "error", detail: "Not a PDF file" };
    if (file.size > MAX_UPLOAD_BYTES) {
      return { ...base, state: "error", detail: "Larger than 20 MB" };
    }
    if (file.size === 0) return { ...base, state: "error", detail: "The file is empty" };
    const key = fileKey(file);
    if (seen.has(key)) {
      return { ...base, state: "duplicate", detail: "Repeated in this batch" };
    }
    seen.add(key);
    return { ...base, state: "queued" };
  });
}

export type UploadFn = (file: File, signal: AbortSignal) => Promise<UploadResponse>;

export interface RunOptions {
  upload: UploadFn;
  onChange: (index: number, patch: Partial<UploadItem>) => void;
  signal: AbortSignal;
  concurrency?: number;
}

/**
 * Sends every queued item, never more than `concurrency` at once. A failure
 * only settles its own item: the rest of the batch carries on. Resolves when
 * every item has an answer (or the batch was aborted).
 */
export async function runUploads(
  items: readonly UploadItem[],
  files: readonly File[],
  { upload, onChange, signal, concurrency = MAX_CONCURRENT_UPLOADS }: RunOptions,
): Promise<void> {
  const pending = items.filter((item) => item.state === "queued").map((item) => item.index);
  let cursor = 0;

  async function worker(): Promise<void> {
    while (cursor < pending.length && !signal.aborted) {
      const index = pending[cursor++] as number;
      const file = files[index] as File;
      onChange(index, { state: "uploading" });
      try {
        const result = await upload(file, signal);
        onChange(
          index,
          result.duplicate
            ? { state: "duplicate", documentId: result.document_id, detail: "Already uploaded" }
            : { state: "sent", documentId: result.document_id },
        );
      } catch (error) {
        if (signal.aborted) return;
        onChange(index, { state: "error", detail: toApiError(error).detail });
      }
    }
  }

  const workers = Array.from({ length: Math.min(concurrency, pending.length) }, worker);
  await Promise.all(workers);
}

export interface BatchSummary {
  total: number;
  sent: number;
  duplicate: number;
  error: number;
}

export function summarize(items: readonly UploadItem[]): BatchSummary {
  const count = (state: UploadState) => items.filter((item) => item.state === state).length;
  return {
    total: items.length,
    sent: count("sent"),
    duplicate: count("duplicate"),
    error: count("error"),
  };
}

export function describeSummary(summary: BatchSummary): string {
  const parts = [`${summary.sent} sent`];
  if (summary.duplicate) parts.push(`${summary.duplicate} duplicate`);
  if (summary.error) parts.push(`${summary.error} failed`);
  return parts.join(", ");
}
