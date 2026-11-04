import type { Confidence, FoundBy, InsufficientReason, QueryPhase } from "../../api/types";
import type { Tone } from "../documents/status";

/** Used when the server sends a phase without a label. */
export const PHASE_LABELS: Record<QueryPhase, string> = {
  contextualizing: "Understanding your follow-up",
  searching: "Searching your documents",
  drafting: "Drafting the answer",
  validating: "Checking citations",
};

/** Progress shown under the phase label, as a share of the pipeline. */
export const PHASE_PROGRESS: Record<QueryPhase, number> = {
  contextualizing: 0.15,
  searching: 0.35,
  drafting: 0.65,
  validating: 0.9,
};

export const CONFIDENCE: Record<Confidence, { label: string; tone: Tone }> = {
  high: { label: "High confidence", tone: "success" },
  medium: { label: "Medium confidence", tone: "processing" },
  low: { label: "Low confidence", tone: "warning" },
};

/** Warnings come as machine codes; same texts as the backend's WARNING_LABELS. */
export const WARNING_LABELS: Readonly<Record<string, string>> = {
  prompt_injection: "A passage that looked like instructions to the assistant was ignored.",
  sources_disagree: "The cited documents give different values; check which one applies.",
};

/** An unknown code (a newer backend) is shown as it comes. */
export const warningLabel = (code: string): string => WARNING_LABELS[code] ?? code;

export const FOUND_BY_LABELS: Record<FoundBy, string> = {
  vector: "Semantic",
  full_text: "Keyword",
  both: "Both",
};

/** A stored turn whose answer was stopped before the server saved it. */
export const INTERRUPTED_TEXT = "Interrupted: the answer was stopped before it was saved.";

/** Why there was no answer, in the user's terms. */
export const INSUFFICIENT_REASONS: Record<InsufficientReason, string> = {
  no_results: "Nothing in your documents matched this question.",
  all_flagged:
    "The only related passages looked like instructions to the assistant, so they were not used.",
  llm_declared: "Related passages were found, but none of them answers the question.",
  validation_failed:
    "An answer was drafted, but its citations could not be checked against your documents, so it was withheld.",
};

export const REPHRASE_HINT =
  "Try rephrasing the question or making it more specific: name the contract, the supplier or the clause you mean.";

/** Closest passages listed under a "not enough evidence" answer. */
export const MAX_RELATED = 3;
