import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { AnswerDetails, Citation, ConversationMessage, RetrievedChunk } from "../../api/types";
import { AnswerGuidance } from "./AnswerGuidance";
import { INSUFFICIENT_REASONS, MAX_RELATED, REPHRASE_HINT } from "./phases";

const chunk = (id: string, doc: string, page: number, flagged = false): RetrievedChunk => ({
  document_id: `doc-${doc}`,
  document_filename: `${doc}.pdf`,
  page,
  chunk_id: `chunk-${id}`,
  score: 0.03,
  found_by: "both",
  flagged,
});

const citation = (id: string, doc: string, page: number): Citation => ({
  chunk_id: `chunk-${id}`,
  document_id: `doc-${doc}`,
  document_filename: `${doc}.pdf`,
  page,
  quote: `quote ${id}`,
});

const details = (overrides: Partial<AnswerDetails>): AnswerDetails => ({
  retrieved: [],
  vector_hits: 0,
  full_text_hits: 0,
  attempts: 1,
  insufficient_reason: null,
  latency_ms: 1200,
  llm_model: "fake-extractive-v3",
  trace_id: null,
  uses_context: false,
  judged: false,
  ...overrides,
});

const message = (overrides: Partial<ConversationMessage>): ConversationMessage => ({
  id: "m1",
  role: "assistant",
  content: "There is not enough information in your documents to answer this question.",
  status: "insufficient_evidence",
  confidence: "low",
  citations: [],
  warnings: [],
  details: details({}),
  created_at: "2026-11-04T10:00:00Z",
  ...overrides,
});

const renderGuidance = (msg: ConversationMessage) =>
  render(
    <MemoryRouter>
      <AnswerGuidance message={msg} />
    </MemoryRouter>,
  );

describe("AnswerGuidance", () => {
  it("explains a refusal, suggests rephrasing and links the closest passages", () => {
    renderGuidance(
      message({
        details: details({
          insufficient_reason: "llm_declared",
          retrieved: [
            chunk("1", "tire", 2),
            chunk("2", "tire", 2), // same document and page: listed once
            chunk("3", "fuel", 4, true), // flagged: never listed as a related passage
            chunk("4", "fuel", 1),
            chunk("5", "carrier", 3),
            chunk("6", "insurance", 5),
          ],
        }),
      }),
    );

    expect(screen.getByText(INSUFFICIENT_REASONS.llm_declared)).toBeInTheDocument();
    expect(screen.getByText(REPHRASE_HINT)).toBeInTheDocument();

    const links = within(screen.getByRole("list", { name: "Closest passages" })).getAllByRole("link");
    expect(links).toHaveLength(MAX_RELATED);
    expect(links.map((link) => link.textContent)).toEqual(["tire.pdf, p. 2", "fuel.pdf, p. 1", "carrier.pdf, p. 3"]);
    expect(links[0]).toHaveAttribute("href", "/documents/doc-tire?page=2&chunk=chunk-1");
    expect(screen.queryByText("fuel.pdf, p. 4")).not.toBeInTheDocument();
  });

  it("gives the reason and the hint even when nothing was found", () => {
    renderGuidance(message({ details: details({ insufficient_reason: "no_results" }) }));

    expect(screen.getByText(INSUFFICIENT_REASONS.no_results)).toBeInTheDocument();
    expect(screen.getByText(REPHRASE_HINT)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Closest passages" })).not.toBeInTheDocument();
  });

  it("does not list flagged passages when they were all that was found", () => {
    renderGuidance(
      message({
        warnings: ["prompt_injection"],
        details: details({ insufficient_reason: "all_flagged", retrieved: [chunk("1", "fuel", 4, true)] }),
      }),
    );

    expect(screen.getByText(INSUFFICIENT_REASONS.all_flagged)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Closest passages" })).not.toBeInTheDocument();
    // Its place (document and page, never its text) is still given, to review the document.
    const excluded = within(screen.getByRole("list", { name: "Excluded passages" })).getByRole("link");
    expect(excluded).toHaveTextContent("fuel.pdf, p. 4");
  });

  it("shows the hint without a reason for answers stored before the reason existed", () => {
    renderGuidance(message({ details: null }));

    expect(screen.getByText(REPHRASE_HINT)).toBeInTheDocument();
    for (const reason of Object.values(INSUFFICIENT_REASONS)) {
      expect(screen.queryByText(reason)).not.toBeInTheDocument();
    }
  });

  it("names the documents to compare when sources disagree", () => {
    renderGuidance(
      message({
        status: "answered",
        confidence: "medium",
        content: "The agreement says 48 hours; the exhibit says 72.",
        warnings: ["sources_disagree"],
        citations: [citation("1", "tire_agreement", 2), citation("2", "tire_exhibit_b", 1)],
      }),
    );

    const links = within(screen.getByRole("list", { name: "Disagreeing sources" })).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["tire_agreement.pdf, p. 2", "tire_exhibit_b.pdf, p. 1"]);
    expect(screen.queryByText(REPHRASE_HINT)).not.toBeInTheDocument();
  });

  it("says where the excluded passage was when an answer carries the injection warning", () => {
    renderGuidance(
      message({
        status: "answered",
        confidence: "high",
        content: "The minimum is 180,000 gallons.",
        warnings: ["prompt_injection"],
        citations: [citation("1", "fuel", 1)],
        details: details({ retrieved: [chunk("1", "fuel", 1), chunk("9", "fuel", 4, true)] }),
      }),
    );

    const links = within(screen.getByRole("list", { name: "Excluded passages" })).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["fuel.pdf, p. 4"]);
    expect(links[0]).toHaveAttribute("href", "/documents/doc-fuel?page=4&chunk=chunk-9");
  });

  it("renders nothing for an ordinary answer", () => {
    const { container } = renderGuidance(
      message({
        status: "answered",
        confidence: "high",
        content: "Net 30.",
        citations: [citation("1", "carrier", 2)],
      }),
    );
    expect(container).toBeEmptyDOMElement();
  });
});
