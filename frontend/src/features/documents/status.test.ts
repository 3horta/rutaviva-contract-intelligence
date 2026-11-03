import type { DocumentStatus, FailureCode } from "../../api/types";
import { DOCUMENT_STATUS, DOCUMENT_STATUSES, FAILURE_REASON, failureReason, isTerminal } from "./status";

describe("document status map", () => {
  it("has an entry for every status, in lifecycle order", () => {
    expect(DOCUMENT_STATUSES).toEqual(["pending", "processing", "ready", "failed"]);
    expect(Object.keys(DOCUMENT_STATUS).sort()).toEqual([...DOCUMENT_STATUSES].sort());
  });

  it.each<[DocumentStatus, boolean]>([
    ["pending", false],
    ["processing", false],
    ["ready", true],
    ["failed", true],
  ])("isTerminal(%s) is %s", (status, terminal) => {
    expect(isTerminal(status)).toBe(terminal);
  });

  it("gives each status a distinct label and tone", () => {
    const labels = DOCUMENT_STATUSES.map((s) => DOCUMENT_STATUS[s].label);
    const tones = DOCUMENT_STATUSES.map((s) => DOCUMENT_STATUS[s].tone);
    expect(new Set(labels).size).toBe(labels.length);
    expect(new Set(tones).size).toBe(tones.length);
  });
});

describe("failure reasons", () => {
  const codes: FailureCode[] = [
    "corrupt_pdf",
    "empty_pdf",
    "embedding_unavailable",
    "timeout",
    "worker_lost",
    "internal_error",
  ];

  it("explains every failure code", () => {
    expect(Object.keys(FAILURE_REASON).sort()).toEqual([...codes].sort());
    codes.forEach((code) => expect(failureReason(code)).toMatch(/\w+/));
  });

  it("has a generic text when the code is missing", () => {
    expect(failureReason(null)).toBe("The document could not be processed.");
  });
});
