/**
 * In-memory backend for `VITE_USE_MOCKS=true`. It answers at the fetch level,
 * so the real client, error parsing and query code all run unchanged.
 * Follows the API contract: Bearer auth per tenant, Problem Details errors,
 * server-side paging/sorting/search, documents that move through statuses.
 */
import { API_PREFIX, setFetchImplementation } from "../api/client";
import type {
  DocumentList,
  DocumentOut,
  DocumentStats,
  DocumentStatus,
  FailureCode,
  UploadResponse,
} from "../api/types";
import { DOCUMENT_ORDERINGS } from "../api/types";
import { decodeClaims, isExpired } from "../auth/jwt";
import { EMBEDDING_MODEL, seedTenants, type MockDocument } from "./data";

const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

const startedAt = Date.now();
const tenants = seedTenants(startedAt);
let uploadCounter = 0;

const randomHex = (length: number) =>
  Array.from({ length }, () => Math.floor(Math.random() * 16).toString(16)).join("");

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function problem(status: number, title: string, detail: string): Response {
  return new Response(
    JSON.stringify({ type: "about:blank", title, status, detail, trace_id: randomHex(32) }),
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}

function delay(ms: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    });
  });
}

/** The document as the API would report it now, applying its timeline. */
function current(mock: MockDocument, now = Date.now()): DocumentOut {
  const timeline = mock.timeline;
  if (!timeline) return mock.doc;
  if (now >= timeline.doneAt) {
    mock.doc = { ...mock.doc, ...timeline.final };
    mock.timeline = undefined;
    return mock.doc;
  }
  if (now >= timeline.processingAt && mock.doc.status === "pending") {
    mock.doc = { ...mock.doc, status: "processing", updated_at: new Date(timeline.processingAt).toISOString() };
  }
  return mock.doc;
}

function compare(ordering: string) {
  const descending = ordering.startsWith("-");
  const field = (descending ? ordering.slice(1) : ordering) as "created_at" | "filename" | "status";
  return (a: DocumentOut, b: DocumentOut) => {
    const result = a[field].localeCompare(b[field]);
    return descending ? -result : result;
  };
}

function listDocuments(docs: MockDocument[], params: URLSearchParams): Response {
  const limit = Number(params.get("limit") ?? 10);
  const offset = Number(params.get("offset") ?? 0);
  const ordering = params.get("ordering") ?? "-created_at";
  const search = params.get("search")?.toLowerCase();
  const status = params.get("status");

  if (!Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0) {
    return problem(422, "Validation error", "limit must be 1-100 and offset must be 0 or more.");
  }
  if (!(DOCUMENT_ORDERINGS as readonly string[]).includes(ordering)) {
    return problem(422, "Validation error", `Unknown ordering "${ordering}".`);
  }

  const results = docs
    .map((mock) => current(mock))
    .filter((doc) => !search || doc.filename.toLowerCase().includes(search))
    .filter((doc) => !status || doc.status === status)
    .sort(compare(ordering));

  const body: DocumentList = { count: results.length, limit, offset, results: results.slice(offset, offset + limit) };
  return json(body);
}

function stats(docs: MockDocument[]): Response {
  const byStatus: Record<DocumentStatus, number> = { pending: 0, processing: 0, ready: 0, failed: 0 };
  docs.forEach((mock) => byStatus[current(mock).status]++);
  const body: DocumentStats = { total: docs.length, by_status: byStatus };
  return json(body);
}

/** What the worker would decide for an uploaded file, from hints in its name. */
function outcomeFor(filename: string): FailureCode | null {
  const name = filename.toLowerCase();
  if (name.includes("corrupt")) return "corrupt_pdf";
  if (name.includes("scan")) return "empty_pdf";
  if (name.includes("timeout")) return "timeout";
  return null;
}

async function upload(docs: MockDocument[], body: BodyInit | null | undefined): Promise<Response> {
  const file = body instanceof FormData ? body.get("file") : null;
  if (!(file instanceof File)) return problem(422, "Validation error", "The form field \"file\" is required.");
  if (file.size > MAX_UPLOAD_BYTES) {
    return problem(413, "Payload too large", "The file is larger than the 20 MB limit.");
  }
  const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
  if (!isPdf) return problem(415, "Unsupported media type", "Only PDF files are accepted.");
  if (file.name.toLowerCase().includes("error")) {
    return problem(500, "Internal server error", "The file could not be stored. Try again.");
  }

  // The API identifies a duplicate by content hash; name and size stand in for it here.
  const existing = docs.find(
    (mock) => mock.doc.filename === file.name && mock.size === file.size && current(mock).status !== "failed",
  );
  if (existing) {
    const response: UploadResponse = { document_id: existing.doc.id, status: current(existing).status, duplicate: true };
    return json(response, 200);
  }

  const bytes = new Uint8Array(await file.arrayBuffer());
  const now = Date.now();
  const id = `0e${randomHex(6)}-${randomHex(4)}-4000-8000-${randomHex(12)}`;
  const failure = outcomeFor(file.name);
  const chunkCount = 6 + (++uploadCounter % 9);
  const doc: DocumentOut = {
    id,
    filename: file.name,
    status: "pending",
    failure_code: null,
    failure_detail: null,
    page_count: null,
    chunk_count: null,
    embedding_model: null,
    metrics: null,
    created_at: new Date(now).toISOString(),
    updated_at: new Date(now).toISOString(),
  };
  docs.push({
    doc,
    // Uploaded files keep their real bytes; they have no mock chunks to cite.
    chunks: [],
    file: () => bytes,
    size: file.size,
    timeline: {
      processingAt: now + 2_000,
      doneAt: now + 6_000 + Math.random() * 4_000,
      final: failure
        ? { status: "failed", failure_code: failure, failure_detail: null, updated_at: new Date(now + 6_000).toISOString() }
        : {
            status: "ready",
            page_count: 1 + (uploadCounter % 6),
            chunk_count: chunkCount,
            embedding_model: EMBEDDING_MODEL,
            metrics: {
              processing_ms: 3_500 + Math.round(Math.random() * 4_000),
              embedding_calls: Math.ceil(chunkCount / 8),
              embedding_tokens: chunkCount * 190,
              tokens_estimated: true,
            },
            updated_at: new Date(now + 6_000).toISOString(),
          },
    },
  });
  const response: UploadResponse = { document_id: id, status: "pending", duplicate: false };
  return json(response, 202);
}

async function handle(path: string, init: RequestInit): Promise<Response> {
  const auth = new Headers(init.headers).get("Authorization") ?? "";
  const claims = auth.startsWith("Bearer ") ? decodeClaims(auth.slice(7)) : null;
  if (!claims || isExpired(claims) || !claims.tenant_id) {
    return problem(401, "Unauthorized", "The access token is missing, invalid or expired.");
  }
  // A tenant the mock does not know simply has no documents yet.
  const docs = tenants.get(claims.tenant_id) ?? [];
  if (!tenants.has(claims.tenant_id)) tenants.set(claims.tenant_id, docs);

  const url = new URL(path, "http://mock.local");
  const method = (init.method ?? "GET").toUpperCase();
  const parts = url.pathname.split("/").filter(Boolean);

  if (parts[0] !== "documents") return problem(404, "Not found", `No route for ${url.pathname}.`);

  if (parts.length === 1 && method === "POST") return upload(docs, init.body);
  if (parts.length === 1 && method === "GET") return listDocuments(docs, url.searchParams);
  if (parts.length === 2 && parts[1] === "stats") return stats(docs);

  // Another tenant's document is a 404, exactly like a missing one.
  const mock = docs.find((m) => m.doc.id === parts[1]);
  if (!mock) return problem(404, "Not found", "Document not found.");

  if (parts.length === 2) return json(current(mock));
  if (parts.length === 3 && parts[2] === "file") {
    return new Response(mock.file(), { status: 200, headers: { "Content-Type": "application/pdf" } });
  }
  if (parts.length === 4 && parts[2] === "chunks") {
    const chunk = mock.chunks.find((c) => c.id === parts[3]);
    return chunk ? json(chunk) : problem(404, "Not found", "Chunk not found.");
  }
  return problem(404, "Not found", `No route for ${url.pathname}.`);
}

async function mockFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const path = input.startsWith(API_PREFIX) ? input.slice(API_PREFIX.length) : input;
  const isUpload = (init.method ?? "GET").toUpperCase() === "POST";
  await delay(isUpload ? 500 + Math.random() * 1_200 : 120 + Math.random() * 280, init.signal);
  return handle(path, init);
}

export function installMockApi(): void {
  setFetchImplementation(mockFetch);
  console.info("[mocks] API served in memory. Demo chunk link:", demoChunkLink());
}

/** A link to a cited fragment, handy to try the viewer in mock mode. */
export function demoChunkLink(): string {
  const mock = tenants.get("tenant_a")?.[0];
  const chunk = mock?.chunks[4];
  return mock && chunk ? `/documents/${mock.doc.id}?page=${chunk.page}&chunk=${chunk.id}` : "";
}
