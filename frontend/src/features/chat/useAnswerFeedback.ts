import { useEffect, useRef, useState } from "react";
import { sendFeedback } from "../../api/feedback";
import type { FeedbackInput, FeedbackRating, MessageFeedback } from "../../api/types";

interface Options {
  conversationId: string;
  messageId: string;
  /** What is stored for the answer, if anything. */
  initial: MessageFeedback | null;
  /** After each change is stored (to keep the cached conversation in step). */
  onStored?: (feedback: MessageFeedback | null) => void;
}

/**
 * The rating of one answer: shown at once, stored in the background. Requests
 * go one after another, so a quick change of mind is stored in the order it
 * was made; a failed one puts back what was shown before it.
 */
export function useAnswerFeedback({ conversationId, messageId, initial, onStored }: Options) {
  const [feedback, setFeedback] = useState<MessageFeedback | null>(initial);
  /** The comment box is open (after choosing a rating). */
  const [commenting, setCommenting] = useState(false);
  const [draft, setDraft] = useState("");
  const [thanked, setThanked] = useState(false);
  const [failed, setFailed] = useState(false);
  const [sending, setSending] = useState(false);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  function store(next: FeedbackInput | null, previous: MessageFeedback | null): Promise<boolean> {
    setFailed(false);
    setSending(true);
    const run = queue.current.then(async () => {
      try {
        const stored = await sendFeedback(conversationId, messageId, next);
        onStored?.(stored);
        if (mounted.current) setFeedback(stored);
        return true;
      } catch {
        if (mounted.current) {
          setFeedback(previous);
          setFailed(true);
        }
        return false;
      } finally {
        if (mounted.current) setSending(false);
      }
    });
    queue.current = run;
    return run;
  }

  /** Choosing the selected rating again takes it back; another one replaces it. */
  function choose(rating: FeedbackRating) {
    const previous = feedback;
    setThanked(false);
    if (previous?.rating === rating) {
      setFeedback(null);
      setCommenting(false);
      void store(null, previous);
      return;
    }
    // A new rating starts without a comment: the old one was about the other choice.
    setFeedback({ rating, comment: null });
    setDraft("");
    setCommenting(true);
    void store({ rating }, previous);
  }

  /** Stores the comment with the rating; resolves whether the box closed. */
  async function sendComment(): Promise<boolean> {
    if (!feedback) return false;
    const comment = draft.trim();
    const saved = await store({ rating: feedback.rating, comment: comment || null }, feedback);
    if (saved && mounted.current) {
      setCommenting(false);
      setThanked(true);
    }
    return saved;
  }

  /** Closes the box without a comment; the rating itself is already stored. */
  function cancel() {
    setCommenting(false);
    setThanked(!failed);
  }

  return { feedback, commenting, draft, setDraft, thanked, failed, sending, choose, sendComment, cancel };
}

export type AnswerFeedbackState = ReturnType<typeof useAnswerFeedback>;
