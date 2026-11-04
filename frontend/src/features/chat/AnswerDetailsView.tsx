import { ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";
import type { AnswerDetails } from "../../api/types";
import { formatDuration } from "../../lib/format";
import { FOUND_BY_LABELS } from "./phases";
import styles from "./Message.module.css";

/** The steps the pipeline took, told from the details of the answer. */
function steps(details: AnswerDetails): string[] {
  const result: string[] = [];
  if (details.uses_context) result.push("Rewrote the follow-up with the conversation's context");
  result.push(
    `Searched your documents: ${details.vector_hits} semantic and ${details.full_text_hits} keyword matches`,
  );
  if (details.insufficient_reason === "no_results") {
    result.push("Nothing relevant was found, so no answer was drafted");
    return result;
  }
  if (details.insufficient_reason === "all_flagged") {
    result.push("Every fragment found looked like instructions to the assistant and was excluded");
    return result;
  }
  const flagged = details.retrieved.filter((chunk) => chunk.flagged).length;
  if (flagged > 0) {
    result.push(`Excluded ${flagged} ${flagged === 1 ? "fragment" : "fragments"} that looked like instructions`);
  }
  result.push(
    details.attempts > 1 ? `Drafted the answer (${details.attempts} attempts)` : "Drafted the answer",
  );
  if (details.insufficient_reason === "llm_declared") {
    result.push("The fragments found do not answer the question");
  } else if (details.insufficient_reason === "validation_failed") {
    result.push("The citations did not hold up, so the answer was withheld");
  } else {
    result.push("Checked that every citation is in the documents");
  }
  return result;
}

/** "How it was answered": collapsed by default, for whoever wants to check. */
export function AnswerDetailsView({ details }: { details: AnswerDetails }) {
  return (
    <details className={`${styles.details} no-print`}>
      <summary>How it was answered</summary>

      {details.uses_context && details.standalone_question && (
        <p className={styles.interpreted}>Interpreted as: “{details.standalone_question}”</p>
      )}

      <ol className={styles.steps}>
        {steps(details).map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>

      {details.retrieved.length > 0 && (
        <div className={styles.tableWrap}>
          <table className={styles.retrieved}>
            <caption className="sr-only">Fragments retrieved</caption>
            <thead>
              <tr>
                <th scope="col">Document</th>
                <th scope="col">Page</th>
                <th scope="col">Score</th>
                <th scope="col">Found by</th>
              </tr>
            </thead>
            <tbody>
              {details.retrieved.map((chunk) => (
                <tr key={chunk.chunk_id} className={chunk.flagged ? styles.flaggedRow : undefined}>
                  <td>
                    <Link to={`/documents/${chunk.document_id}?page=${chunk.page}&chunk=${chunk.chunk_id}`}>
                      {chunk.document_filename}
                    </Link>
                    {chunk.flagged && (
                      <span className={styles.flagged} title="Looked like instructions to the assistant; not used">
                        <ShieldAlert size={12} aria-hidden="true" />
                        Excluded
                      </span>
                    )}
                  </td>
                  <td>{chunk.page}</td>
                  <td>{chunk.score.toFixed(3)}</td>
                  <td>{FOUND_BY_LABELS[chunk.found_by]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <dl className={styles.facts}>
        <div>
          <dt>Model</dt>
          <dd>{details.llm_model}</dd>
        </div>
        <div>
          <dt>Attempts</dt>
          <dd>{details.attempts}</dd>
        </div>
        <div>
          <dt>Latency</dt>
          <dd>{formatDuration(details.latency_ms)}</dd>
        </div>
        {details.trace_id && (
          <div>
            <dt>Trace id</dt>
            <dd>
              <code>{details.trace_id}</code>
            </dd>
          </div>
        )}
      </dl>
    </details>
  );
}
