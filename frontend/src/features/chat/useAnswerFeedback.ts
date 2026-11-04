import { useEffect, useRef, useState } from "react";
import { sendFeedback } from "../../api/feedback";
import type { FeedbackInput, FeedbackRating, FeedbackTotals, MessageFeedback } from "../../api/types";

const NO_TOTALS: FeedbackTotals = { up: 0, down: 0 };

/** The totals once this rater's rating goes from `from` to `to`, before the server says so. */
function moveTotals(totals: FeedbackTotals, from: FeedbackRating | null, to: FeedbackRating | null): FeedbackTotals {
  const next = { ...totals };
  if (from) next[from] = Math.max(0, next[from] - 1);
  if (to) next[to] += 1;
  return next;
}

interface Options {
  conversationId: string;
  messageId: string;
  /** What this browser's rater has stored for the answer, if anything. */
  initial: MessageFeedback | null;
  /** Ratings of all the tenant's raters. */
  initialTotals: FeedbackTotals | null;
  /** After each change is stored (to keep the cached conversation in step). */
  onStored?: (feedback: MessageFeedback | null, totals: FeedbackTotals) => void;
}

/**
 * This browser's rating of one answer: shown at once, with the totals moved to
 * match, and stored in the background. Requests go one after another, so a
 * quick change of mind is stored in the order it was made; a failed one puts
 * back what was shown before it. The server's totals replace the guess.
 */
export function useAnswerFeedback({ conversationId, messageId, initial, initialTotals, onStored }: Options) {
  const [feedback, setFeedback] = useState<MessageFeedback | null>(initial);
  const [totals, setTotals] = useState<FeedbackTotals>(initialTotals ?? NO_TOTALS);
  /** The comment box is open (after choosing a rating, or to edit the comment). */
  const [commenting, setCommenting] = useState(false);
  const [draft, setDraft] = useState("");
  const [thanked, setThanked] = useState(false);
  const [failed, setFailed] = useState(false);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  function store(next: FeedbackInput | null, previous: MessageFeedback | null, previousTotals: FeedbackTotals) {
    setFailed(false);
    const run = queue.current.then(async () => {
      try {
        const result = await sendFeedback(conversationId, messageId, next);
        onStored?.(result.feedback ?? null, result.totals);
        if (mounted.current) {
          setFeedback(result.feedback ?? null);
          setTotals(result.totals);
        }
        return true;
      } catch {
        if (mounted.current) {
          setFeedback(previous);
          setTotals(previousTotals);
          setFailed(true);
        }
        return false;
      }
    });
    queue.current = run;
    return run;
  }

  /** Choosing the selected rating again takes it back; another one replaces it. */
  function choose(rating: FeedbackRating) {
    const previous = feedback;
    const from = previous?.rating ?? null;
    setThanked(false);
    if (from === rating) {
      setFeedback(null);
      setTotals(moveTotals(totals, from, null));
      setCommenting(false);
      void store(null, previous, totals);
      return;
    }
    // A new rating starts without a comment: the old one was about the other choice.
    setFeedback({ rating, comment: null });
    setTotals(moveTotals(totals, from, rating));
    setDraft("");
    setCommenting(true);
    void store({ rating }, previous, totals);
  }

  /** Opens the box again with the saved comment. */
  function editComment() {
    setThanked(false);
    setDraft(feedback?.comment ?? "");
    setCommenting(true);
  }

  /** Stores the comment with the rating; resolves whether the box closed. */
  async function sendComment(): Promise<boolean> {
    if (!feedback) return false;
    const comment = draft.trim();
    const saved = await store({ rating: feedback.rating, comment: comment || null }, feedback, totals);
    if (saved && mounted.current) {
      setCommenting(false);
      setThanked(true);
    }
    return saved;
  }

  /** Closes the box without changing the comment; the rating itself is already stored. */
  function cancel() {
    setCommenting(false);
    setThanked(!failed);
  }

  return {
    feedback,
    totals,
    commenting,
    draft,
    setDraft,
    thanked,
    failed,
    choose,
    editComment,
    sendComment,
    cancel,
  };
}

export type AnswerFeedbackState = ReturnType<typeof useAnswerFeedback>;
