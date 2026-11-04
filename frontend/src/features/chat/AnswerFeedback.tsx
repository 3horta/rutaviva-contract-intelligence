import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useId, useRef, type RefObject } from "react";
import { FEEDBACK_COMMENT_MAX, type FeedbackRating } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { Textarea } from "../../components/ui/Field";
import type { AnswerFeedbackState } from "./useAnswerFeedback";
import styles from "./Message.module.css";

const RATINGS: { rating: FeedbackRating; label: string; Icon: typeof ThumbsUp }[] = [
  { rating: "up", label: "Useful", Icon: ThumbsUp },
  { rating: "down", label: "Not useful", Icon: ThumbsDown },
];

interface ButtonsProps {
  state: AnswerFeedbackState;
  locked: boolean;
  groupRef: RefObject<HTMLDivElement>;
}

/** 👍 / 👎 toggles, with the short confirmation (or error) next to them. */
export function FeedbackButtons({ state, locked, groupRef }: ButtonsProps) {
  const chosen = state.feedback?.rating ?? null;
  return (
    <>
      <div ref={groupRef} className={styles.rating} role="group" aria-label="Rate this answer">
        {RATINGS.map(({ rating, label, Icon }) => {
          const pressed = chosen === rating;
          return (
            <Button
              key={rating}
              variant="text"
              size="small"
              iconOnly
              className={pressed ? styles.pressed : undefined}
              aria-pressed={pressed}
              aria-label={label}
              title={label}
              disabled={locked}
              onClick={() => state.choose(rating)}
              icon={<Icon size={15} aria-hidden="true" fill={pressed ? "currentColor" : "none"} />}
            />
          );
        })}
      </div>
      <span className={styles.feedbackNote} role="status">
        {state.thanked ? "Thanks for the feedback" : ""}
      </span>
      {state.failed && (
        <span className={styles.feedbackError} role="alert">
          The feedback could not be saved. Try again.
        </span>
      )}
    </>
  );
}

interface CommentProps {
  state: AnswerFeedbackState;
  /** The box closed (sent or cancelled): focus goes back to the rating. */
  onClosed: () => void;
}

/** The optional comment, asked right after a rating. */
export function FeedbackComment({ state, onClosed }: CommentProps) {
  const id = useId();
  const boxRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => boxRef.current?.focus(), []);

  const label = state.feedback?.rating === "down" ? "What went wrong?" : "What was useful?";

  function cancel() {
    state.cancel();
    onClosed();
  }

  return (
    <form
      className={`${styles.feedbackForm} no-print`}
      aria-label="Feedback comment"
      onSubmit={(event) => {
        event.preventDefault();
        void state.sendComment().then((closed) => closed && onClosed());
      }}
    >
      <label className={styles.feedbackLabel} htmlFor={id}>
        {label} <span className={styles.optional}>(optional)</span>
      </label>
      <Textarea
        ref={boxRef}
        id={id}
        rows={2}
        maxLength={FEEDBACK_COMMENT_MAX}
        value={state.draft}
        onChange={(event) => state.setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            cancel();
          }
        }}
      />
      <div className={styles.feedbackFormActions}>
        <Button type="submit" variant="primary" size="small" disabled={state.sending}>
          Send
        </Button>
        <Button variant="text" size="small" onClick={cancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
