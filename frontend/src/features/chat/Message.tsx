import { AlertTriangle, Check, Copy, FileText, MessageCirclePlus, RotateCcw, SearchX, Square } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { ApiError } from "../../api/errors";
import type { Citation, ConversationMessage, PhaseUpdate } from "../../api/types";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ErrorAlert, Spinner } from "../../components/ui/Feedback";
import { copyText } from "../../lib/clipboard";
import { AnswerDetailsView } from "./AnswerDetailsView";
import { Markdown } from "./Markdown";
import { CONFIDENCE, INTERRUPTED_TEXT, PHASE_LABELS, PHASE_PROGRESS, warningLabel } from "./phases";
import styles from "./Message.module.css";

export function UserMessage({ content }: { content: string }) {
  return (
    <div className={styles.userRow}>
      <p className={styles.question}>{content}</p>
    </div>
  );
}

const citationLink = (citation: Citation) =>
  `/documents/${citation.document_id}?page=${citation.page}&chunk=${citation.chunk_id}`;

function Citations({ citations }: { citations: Citation[] }) {
  return (
    <section className={styles.sources} aria-label="Sources">
      <h3 className={styles.sourcesTitle}>Sources</h3>
      <ol className={styles.citations}>
        {citations.map((citation, index) => (
          <li key={`${citation.chunk_id}-${index}`}>
            <Link to={citationLink(citation)} className={styles.citation}>
              <FileText size={13} aria-hidden="true" />
              <span>
                {citation.document_filename}, p. {citation.page}
              </span>
            </Link>
            <blockquote className={styles.quote}>{citation.quote}</blockquote>
          </li>
        ))}
      </ol>
    </section>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1_500);
    return () => window.clearTimeout(timer);
  }, [copied]);

  return (
    <Button
      variant="text"
      size="small"
      iconOnly
      aria-label={copied ? "Copied" : "Copy answer"}
      title={copied ? "Copied" : "Copy answer"}
      onClick={() => void copyText(text).then(setCopied)}
      icon={copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
    />
  );
}

interface AssistantMessageProps {
  message: ConversationMessage;
  /** Only the last answer can be asked again. */
  onRetry?: () => void;
  /** Nothing can be changed while another answer is being written. */
  locked: boolean;
}

export function AssistantMessage({ message, onRetry, locked }: AssistantMessageProps) {
  const insufficient = message.status === "insufficient_evidence";
  const confidence = message.confidence && !insufficient ? CONFIDENCE[message.confidence] : null;

  return (
    <article className={`${styles.answer} ${insufficient ? styles.insufficient : ""}`} aria-label="Answer">
      {insufficient && (
        <p className={styles.insufficientTitle}>
          <SearchX size={16} aria-hidden="true" />
          Not enough evidence
        </p>
      )}
      <Markdown text={message.content} />

      {confidence && (
        <div className={styles.meta}>
          <Badge tone={confidence.tone}>{confidence.label}</Badge>
        </div>
      )}

      {message.warnings.length > 0 && (
        <ul className={styles.warnings} aria-label="Warnings">
          {message.warnings.map((warning) => (
            <li key={warning}>
              <AlertTriangle size={14} aria-hidden="true" />
              {warningLabel(warning)}
            </li>
          ))}
        </ul>
      )}

      {message.citations.length > 0 && <Citations citations={message.citations} />}
      {message.details && <AnswerDetailsView details={message.details} />}

      <div className={`${styles.actions} no-print`}>
        <CopyButton text={message.content} />
        {onRetry && (
          <Button
            variant="text"
            size="small"
            iconOnly
            aria-label="Ask again"
            title="Ask again"
            disabled={locked}
            onClick={onRetry}
            icon={<RotateCcw size={15} aria-hidden="true" />}
          />
        )}
      </div>
    </article>
  );
}

/** The answer being written: the current phase and how far along it is. */
export function PendingAnswer({ phase }: { phase: PhaseUpdate | null }) {
  const label = phase ? phase.label || PHASE_LABELS[phase.phase] : "Connecting";
  const progress = phase ? PHASE_PROGRESS[phase.phase] : 0.05;
  return (
    <div className={styles.answer}>
      <div className={styles.progress}>
        <Spinner size={16} />
        <span>{label}…</span>
      </div>
      <div
        className={styles.progressBar}
        role="progressbar"
        aria-label="Answer progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress * 100)}
      >
        <span style={{ width: `${progress * 100}%` }} />
      </div>
    </div>
  );
}

/**
 * A failed answer. A request the server rejected (4xx, e.g. a 422) would fail
 * the same way again, so only network and server errors (0, 5xx) offer Retry.
 */
export function FailedAnswer({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const retryable = error.status === 0 || error.status >= 500;
  const title =
    error.status === 422
      ? "The question was not accepted"
      : error.status === 503 || error.status === 504
        ? "The answer service is not available right now"
        : "The answer could not be completed";
  return (
    <div className={styles.answer}>
      <ErrorAlert
        error={error}
        title={title}
        actions={
          retryable && (
            <Button size="small" onClick={onRetry} icon={<RotateCcw size={14} aria-hidden="true" />}>
              Retry
            </Button>
          )
        }
      />
    </div>
  );
}

/** The turn just stopped, until the conversation is reloaded with what the server kept. */
export function InterruptedNote() {
  return (
    <p className={styles.interrupted} role="status">
      <Square size={12} aria-hidden="true" />
      Answer stopped. The question is kept in this conversation.
    </p>
  );
}

interface InterruptedAnswerProps {
  /** Asks the question again (replacing this turn when it is the last one). */
  onAskAgain: () => void;
  locked: boolean;
}

/** A stored turn without an answer: it was stopped before the server saved one. */
export function InterruptedAnswer({ onAskAgain, locked }: InterruptedAnswerProps) {
  return (
    <article className={`${styles.answer} ${styles.interruptedAnswer}`} aria-label="Interrupted answer">
      <p className={styles.interrupted}>
        <Square size={12} aria-hidden="true" />
        {INTERRUPTED_TEXT}
      </p>
      <div className={`${styles.actions} no-print`}>
        <Button
          variant="text"
          size="small"
          disabled={locked}
          onClick={onAskAgain}
          icon={<RotateCcw size={14} aria-hidden="true" />}
        >
          Ask again
        </Button>
      </div>
    </article>
  );
}

/** Follow-up questions the server suggested for the last answer. */
export function Suggestions({ suggestions, onAsk }: { suggestions: string[]; onAsk: (question: string) => void }) {
  return (
    <section className={`${styles.suggestions} no-print`} aria-label="Suggested questions">
      <h3 className={styles.suggestionsTitle}>
        <MessageCirclePlus size={14} aria-hidden="true" />
        You could also ask
      </h3>
      <ul className={styles.suggestionList}>
        {suggestions.map((suggestion) => (
          <li key={suggestion}>
            <button type="button" className={styles.suggestion} onClick={() => onAsk(suggestion)}>
              {suggestion}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
