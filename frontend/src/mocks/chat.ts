/**
 * Questions and conversations of the in-memory backend. Answers are built
 * from the mock contracts with a keyword match: no model, but real chunk ids,
 * so every citation opens the right page of the right document.
 *
 * Try: a question about the contracts ("What is the payment term in the fuel
 * agreement?"), one they cannot answer ("What is the weather in Laredo?"),
 * one with "outage" (503) or "timeout" (504) in it, which fail mid-stream,
 * a question about several suppliers (warning "sources_disagree") and one about
 * "instructions" (a flagged fragment, warning "prompt_injection").
 */
import type {
  AnswerDetails,
  ChunkOut,
  Citation,
  ConversationDetail,
  ConversationList,
  ConversationMessage,
  ConversationSummary,
  DocumentOut,
  QueryPhase,
  QueryResponse,
  RetrievedChunk,
} from "../api/types";
import { INSUFFICIENT_EVIDENCE_ANSWER } from "../api/types";

export interface MockConversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: ConversationMessage[];
}

/** A ready document with its chunks: what the answers can cite. */
export interface Searchable {
  doc: DocumentOut;
  chunks: ChunkOut[];
}

const PHASE_LABELS: Record<QueryPhase, string> = {
  contextualizing: "Understanding your follow-up",
  searching: "Searching your documents",
  drafting: "Drafting the answer",
  validating: "Checking the citations",
};

const MOCK_MODEL = "mock-llm";

const STOPWORDS = new Set(
  (
    "what which when where who whom whose why how does do did is are was were be been the a an " +
    "and or of in on for to from with by at about into this that these those there their our your " +
    "any all can could should would will shall have has had we us it its if than then them they " +
    "agreement agreements contract contracts document documents supplier buyer tell show give me"
  ).split(" "),
);

const randomHex = (length: number) =>
  Array.from({ length }, () => Math.floor(Math.random() * 16).toString(16)).join("");
const uuid = () => `${randomHex(8)}-${randomHex(4)}-4${randomHex(3)}-a${randomHex(3)}-${randomHex(12)}`;

/** Words worth searching for, cut to a short stem ("payments" -> "payme"). */
function keywords(text: string): string[] {
  const words = text.toLowerCase().match(/[a-z]+/g) ?? [];
  return [...new Set(words.filter((w) => w.length > 2 && !STOPWORDS.has(w)).map((w) => w.slice(0, 5)))];
}

function excerpt(text: string, max = 220): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  return `${cut.slice(0, cut.lastIndexOf(" "))}…`;
}

function firstSentence(text: string): string {
  const match = /^.*?[.;](\s|$)/.exec(text);
  return excerpt((match?.[0] ?? text).trim(), 200);
}

const titleOf = (filename: string) => filename.replace(/\.pdf$/i, "").replace(/_/g, " ");

/** What the backend would answer: same shape, built from keyword matches. */
export function answer(question: string, docs: Searchable[], previousQuestion: string | null) {
  const usesContext = previousQuestion !== null;
  const standalone = usesContext ? `${previousQuestion} ${question}` : null;
  const words = keywords(standalone ?? question);

  // A document named in the question narrows the search to it, as a user expects.
  const named = docs.filter((d) => words.some((w) => d.doc.filename.toLowerCase().includes(w)));
  const pool = named.length ? named : docs;

  const ranked = pool
    .flatMap((d) =>
      d.chunks.map((chunk) => ({
        doc: d.doc,
        chunk,
        score: words.filter((w) => chunk.content.toLowerCase().includes(w)).length,
      })),
    )
    .filter((hit) => hit.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 6);

  const best = ranked[0]?.score ?? 0;
  const retrieved: RetrievedChunk[] = ranked.map((hit, index) => ({
    document_id: hit.doc.id,
    document_filename: hit.doc.filename,
    page: hit.chunk.page,
    chunk_id: hit.chunk.id,
    score: Math.round((0.92 - index * 0.07) * 1000) / 1000,
    found_by: hit.score >= 2 ? "both" : index % 2 ? "full_text" : "vector",
    flagged: false,
  }));
  const details: AnswerDetails = {
    standalone_question: standalone,
    uses_context: usesContext,
    retrieved,
    vector_hits: retrieved.filter((r) => r.found_by !== "full_text").length,
    full_text_hits: retrieved.filter((r) => r.found_by !== "vector").length,
    attempts: 1,
    insufficient_reason: null,
    latency_ms: 1_800 + Math.round(Math.random() * 2_400),
    llm_model: MOCK_MODEL,
    trace_id: randomHex(32),
  };

  // Nothing found, or only loose matches for a specific question: not enough evidence.
  if (!ranked.length || (best < 2 && words.length >= 3)) {
    return {
      answer: INSUFFICIENT_EVIDENCE_ANSWER,
      status: "insufficient_evidence" as const,
      confidence: "low" as const,
      citations: [] as Citation[],
      warnings: [] as string[],
      details: { ...details, insufficient_reason: ranked.length ? ("llm_declared" as const) : ("no_results" as const) },
    };
  }

  // A question about "instructions" shows the guardrail: the last fragment is
  // flagged as a possible prompt injection and never cited.
  const injected = /\binstructions?\b/i.test(question) && retrieved.length > 1;
  const last = retrieved.at(-1);
  if (injected && last) retrieved[retrieved.length - 1] = { ...last, flagged: true };

  const cited = ranked.slice(0, Math.min(3, injected ? ranked.length - 1 : ranked.length));
  const citations: Citation[] = cited.map((hit) => ({
    chunk_id: hit.chunk.id,
    document_id: hit.doc.id,
    document_filename: hit.doc.filename,
    page: hit.chunk.page,
    quote: excerpt(hit.chunk.content),
  }));
  const confidence = cited.length >= 3 && best >= 3 ? "high" : cited.length >= 2 ? "medium" : "low";
  const documentCount = new Set(cited.map((hit) => hit.doc.id)).size;

  // Machine codes, as the backend sends them (the UI has the texts).
  const warnings: string[] = [];
  if (injected) warnings.push("prompt_injection");
  if (documentCount > 1) warnings.push("sources_disagree");

  const text = [
    `Based on ${documentCount === 1 ? "the contract" : "your contracts"}, this is what applies:`,
    "",
    ...cited.map((hit) => `- **${titleOf(hit.doc.filename)}** (page ${hit.chunk.page}): ${firstSentence(hit.chunk.content)}`),
    "",
    "Check the cited fragments for the exact wording before relying on it.",
  ].join("\n");

  return {
    answer: text,
    status: "answered" as const,
    confidence,
    citations,
    warnings,
    details: { ...details, attempts: confidence === "low" ? 2 : 1 },
  } satisfies Omit<QueryResponse, "conversation_id" | "message_id">;
}

function titleFor(question: string): string {
  const clean = question.trim().replace(/\s+/g, " ");
  return clean.length > 60 ? `${clean.slice(0, 57)}…` : clean;
}

function summary(conversation: MockConversation): ConversationSummary {
  return {
    id: conversation.id,
    title: conversation.title,
    created_at: conversation.created_at,
    updated_at: conversation.updated_at,
    message_count: conversation.messages.length,
  };
}

const lastQuestion = (conversation: MockConversation) =>
  [...conversation.messages].reverse().find((m) => m.role === "user")?.content ?? null;

/** Appends a question and its answer, as the backend stores a finished turn. */
function storeTurn(
  conversation: MockConversation,
  question: string,
  result: ReturnType<typeof answer>,
  at: number,
): QueryResponse {
  const createdAt = new Date(at).toISOString();
  const messageId = uuid();
  const { answer: content, ...fields } = result;
  conversation.messages.push(
    {
      id: uuid(),
      role: "user",
      content: question,
      status: null,
      confidence: null,
      citations: [],
      warnings: [],
      details: null,
      created_at: createdAt,
    },
    { id: messageId, role: "assistant", content, created_at: createdAt, ...fields },
  );
  conversation.updated_at = createdAt;
  return { conversation_id: conversation.id, message_id: messageId, ...result };
}

const SEEDED_CONVERSATIONS: { hoursAgo: number; questions: string[] }[] = [
  { hoursAgo: 1, questions: ["What is the payment term in the fuel supply agreement?", "And is there interest on late payments?"] },
  { hoursAgo: 26, questions: ["How does the tire supply agreement renew?"] },
  { hoursAgo: 70, questions: ["What liability insurance must the maintenance supplier carry?"] },
  { hoursAgo: 120, questions: ["Can we terminate the warehouse lease for convenience?"] },
  { hoursAgo: 300, questions: ["What service credits apply if a service level is missed?"] },
  { hoursAgo: 480, questions: ["Which courts have jurisdiction over disputes with customs brokerage?"] },
  { hoursAgo: 1_100, questions: ["What is the weather forecast for Laredo next week?"] },
  ...[
    "What is the price review cap for the trailer rental agreement?",
    "Who must be named as additional insured in the cargo insurance policy?",
    "What notice is needed for non-renewal of the fuel card program?",
    "What is the hourly bill rate for driver staffing overtime?",
    "How long is the initial term of the telematics subscription?",
    "What are the payment terms of the eld hardware purchase?",
    "What labor rate applies to reefer maintenance?",
    "How is the cross dock lease rent escalated?",
    "What fee applies per customs entry?",
    "What happens if the chassis rental supplier misses service levels?",
    "Is there a limitation of liability in the retread program?",
    "What daily rate applies to reefer trailers?",
    "What exhibits form part of the motor carrier agreement?",
    "How are disputes about invoices handled in the fuel supply agreement?",
    "What is the cure period for a material breach in the lease?",
  ].map((question, index) => ({ hoursAgo: 800 + index * 90, questions: [question] })),
];

export function seedConversations(docs: Searchable[], now: number): MockConversation[] {
  return SEEDED_CONVERSATIONS.map(({ hoursAgo, questions }) => {
    const at = now - hoursAgo * 3_600_000;
    const conversation: MockConversation = {
      id: uuid(),
      title: titleFor(questions[0] ?? ""),
      created_at: new Date(at).toISOString(),
      updated_at: new Date(at).toISOString(),
      messages: [],
    };
    questions.forEach((question, index) =>
      storeTurn(conversation, question, answer(question, docs, lastQuestion(conversation)), at + index * 60_000),
    );
    return conversation;
  });
}

export function listConversations(conversations: MockConversation[], params: URLSearchParams): ConversationList | null {
  const limit = Number(params.get("limit") ?? 20);
  const offset = Number(params.get("offset") ?? 0);
  if (!Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0) return null;
  const search = params.get("search")?.trim().toLowerCase();
  const results = conversations
    .filter((c) => !search || c.title.toLowerCase().includes(search))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .map(summary);
  return { count: results.length, limit, offset, results: results.slice(offset, offset + limit) };
}

export function conversationDetail(conversation: MockConversation): ConversationDetail {
  return { ...summary(conversation), messages: conversation.messages };
}

export function renameConversation(conversation: MockConversation, title: string): ConversationSummary {
  conversation.title = title;
  return summary(conversation);
}

/** 1 in the browser; 0 in tests, so answers arrive at once. */
let timeScale = 1;
export function setMockTimeScale(scale: number): void {
  timeScale = scale;
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms * timeScale));

const block = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

const problemBody = (status: number, title: string, detail: string) => ({
  type: "about:blank",
  title,
  status,
  detail,
  trace_id: randomHex(32),
});

interface StreamInput {
  question: string;
  conversation: MockConversation;
  retry: boolean;
  docs: Searchable[];
  signal?: AbortSignal | null;
  /** Called when the turn is stored: a new conversation exists from then on. */
  onStored: () => void;
}

/** How long each phase lasts in the mock, in ms. */
const PHASE_MS: Record<QueryPhase, number> = {
  contextualizing: 700,
  searching: 900,
  drafting: 1_400,
  validating: 800,
};

/**
 * The answer as an SSE body: conversation, phases, then the result or an
 * error. Like the real backend, a client that disconnects (Stop) does not
 * stop the work: the turn is still stored when the answer completes.
 */
export function streamAnswer({ question, conversation, retry, docs, signal, onStored }: StreamInput): Response {
  const encoder = new TextEncoder();
  let detached = false;
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (text: string) => {
        if (!detached) controller.enqueue(encoder.encode(text));
      };
      const close = () => {
        if (!detached) controller.close();
      };
      signal?.addEventListener("abort", () => {
        if (detached) return;
        detached = true;
        controller.error(new DOMException("Aborted", "AbortError"));
      });

      // A retry answers again as if the last turn had not happened.
      const kept = retry ? conversation.messages.slice(0, -2) : conversation.messages;
      const previous = lastQuestion({ ...conversation, messages: kept });

      send(block("conversation", { conversation_id: conversation.id, title: conversation.title }));
      const phases: QueryPhase[] = previous !== null ? ["contextualizing"] : [];
      phases.push("searching", "drafting", "validating");
      for (const phase of phases) {
        send(block("phase", { phase, label: PHASE_LABELS[phase] }));
        await wait(PHASE_MS[phase]);
        if (phase === "searching" && /\boutage\b/i.test(question)) {
          send(block("error", problemBody(503, "Service unavailable", "The language model is not available. Try again in a moment.")));
          return close();
        }
        if (phase === "drafting" && /\btimeout\b/i.test(question)) {
          send(block("error", problemBody(504, "Gateway timeout", "The language model took too long to answer. Try again.")));
          return close();
        }
      }

      if (retry) conversation.messages.splice(-2, 2);
      const result = storeTurn(conversation, question, answer(question, docs, previous), Date.now());
      onStored();
      // Sent in two pieces, cut mid-event, as a network may deliver it.
      const text = block("result", result);
      const cut = Math.floor(text.length / 2);
      send(text.slice(0, cut));
      await wait(60);
      send(text.slice(cut));
      close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

/** Not stored until its first turn is: the real backend only reserves the id. */
export function newConversation(question: string): MockConversation {
  const now = new Date().toISOString();
  return { id: uuid(), title: titleFor(question), created_at: now, updated_at: now, messages: [] };
}

