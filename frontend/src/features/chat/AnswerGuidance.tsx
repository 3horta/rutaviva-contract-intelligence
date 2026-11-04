import { FileText, Lightbulb } from "lucide-react";
import { Link } from "react-router-dom";
import type { ConversationMessage } from "../../api/types";
import { INSUFFICIENT_REASONS, MAX_RELATED, REPHRASE_HINT } from "./phases";
import styles from "./AnswerGuidance.module.css";

/**
 * What to do next with a hard answer, told from data the answer already has
 * (its status, citations, warnings and `details`). It never shows fragment
 * text: only documents and pages, as links to the cited place in the PDF.
 *
 * - Not enough evidence: why, a hint to ask a more specific question, and the
 *   closest passages found (never the ones flagged as prompt injection).
 * - Sources disagree: which documents and pages to compare.
 * - Prompt injection: which document and page the excluded passage was in.
 */

interface Place {
  documentId: string;
  filename: string;
  page: number;
  chunkId: string;
}

const placeLink = (place: Place) =>
  `/documents/${place.documentId}?page=${place.page}&chunk=${place.chunkId}`;

/** One entry per document and page, in the order given. */
function uniquePlaces(places: Place[]): Place[] {
  const seen = new Set<string>();
  return places.filter((place) => {
    const key = `${place.documentId}:${place.page}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function PlaceLinks({ places, label }: { places: Place[]; label: string }) {
  return (
    <ul className={styles.places} aria-label={label}>
      {places.map((place) => (
        <li key={`${place.documentId}:${place.page}`}>
          <Link to={placeLink(place)} className={styles.place}>
            <FileText size={13} aria-hidden="true" />
            <span>
              {place.filename}, p. {place.page}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function AnswerGuidance({ message }: { message: ConversationMessage }) {
  const details = message.details;
  const retrieved = details?.retrieved ?? [];
  const toPlace = (chunk: (typeof retrieved)[number]): Place => ({
    documentId: chunk.document_id,
    filename: chunk.document_filename,
    page: chunk.page,
    chunkId: chunk.chunk_id,
  });

  const insufficient = message.status === "insufficient_evidence";
  const reason = details?.insufficient_reason ?? null;
  // Never a flagged passage here: it is not something to go and read as evidence.
  const related = insufficient
    ? uniquePlaces(retrieved.filter((chunk) => !chunk.flagged).map(toPlace)).slice(0, MAX_RELATED)
    : [];

  const disagreeing = message.warnings.includes("sources_disagree")
    ? uniquePlaces(
        message.citations.map((citation) => ({
          documentId: citation.document_id,
          filename: citation.document_filename,
          page: citation.page,
          chunkId: citation.chunk_id,
        })),
      )
    : [];
  const excluded = message.warnings.includes("prompt_injection")
    ? uniquePlaces(retrieved.filter((chunk) => chunk.flagged).map(toPlace))
    : [];

  if (!insufficient && disagreeing.length < 2 && excluded.length === 0) return null;

  return (
    <div className={styles.guidance}>
      {insufficient && (
        <section className={styles.block} aria-label="Why there is no answer">
          {reason && <p className={styles.reason}>{INSUFFICIENT_REASONS[reason]}</p>}
          <p className={styles.hint}>
            <Lightbulb size={14} aria-hidden="true" />
            {REPHRASE_HINT}
          </p>
          {related.length > 0 && (
            <>
              <p className={styles.label}>Closest passages found (related, but they do not answer it):</p>
              <PlaceLinks places={related} label="Closest passages" />
            </>
          )}
        </section>
      )}

      {disagreeing.length >= 2 && (
        <section className={styles.block} aria-label="Sources to compare">
          <p className={styles.label}>Compare these passages to decide which one applies:</p>
          <PlaceLinks places={disagreeing} label="Disagreeing sources" />
        </section>
      )}

      {excluded.length > 0 && (
        <section className={styles.block} aria-label="Excluded passages">
          <p className={styles.label}>
            {excluded.length === 1
              ? "The ignored passage is here, if you want to review that document:"
              : "The ignored passages are here, if you want to review those documents:"}
          </p>
          <PlaceLinks places={excluded} label="Excluded passages" />
        </section>
      )}
    </div>
  );
}
