import type { Confidence, FoundBy, QueryPhase } from "../../api/types";
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

export const FOUND_BY_LABELS: Record<FoundBy, string> = {
  vector: "Semantic",
  full_text: "Keyword",
  both: "Both",
};
