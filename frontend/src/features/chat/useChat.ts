import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getConversation } from "../../api/conversations";
import { ApiError, toApiError } from "../../api/errors";
import { streamQuery } from "../../api/queryStream";
import type { ConversationDetail, ConversationMessage, PhaseUpdate, QueryResponse } from "../../api/types";
import { conversationKeys } from "./queries";

/** After Stop, the server gets a moment to store what it kept of the turn. */
export const RELOAD_AFTER_STOP_MS = 1_500;

/** The turn on screen that the server has not returned as stored messages yet. */
export interface LiveTurn {
  /** Null until the server announces the conversation of a new chat. */
  conversationId: string | null;
  question: string;
  retry: boolean;
  /**
   * The conversation was created by this turn. The backend stores it only with
   * its first turn, so until then it must not be fetched (it would be a 404).
   */
  unsaved: boolean;
  state: "streaming" | "failed" | "interrupted";
  phase: PhaseUpdate | null;
  error: ApiError | null;
}

/** The conversation with a finished turn, as the server stored it. */
function withTurn(
  detail: ConversationDetail,
  question: string,
  result: QueryResponse,
  retry: boolean,
): ConversationDetail {
  const now = new Date().toISOString();
  const kept = retry ? detail.messages.slice(0, -2) : detail.messages;
  const answerId = result.message_id ?? `local-${Date.now()}`;
  const asked: ConversationMessage = {
    id: `question-${answerId}`,
    role: "user",
    content: question,
    status: null,
    confidence: null,
    citations: [],
    warnings: [],
    details: null,
    suggestions: [],
    created_at: now,
  };
  const answered: ConversationMessage = {
    id: answerId,
    role: "assistant",
    content: result.answer,
    status: result.status,
    confidence: result.confidence,
    citations: result.citations,
    warnings: result.warnings,
    details: result.details ?? null,
    suggestions: [],
    created_at: now,
  };
  return { ...detail, updated_at: now, messages: [...kept, asked, answered] };
}

/** The suggested follow-ups that arrive a moment after their answer. */
function withSuggestions(detail: ConversationDetail, messageId: string, suggestions: string[]): ConversationDetail {
  return {
    ...detail,
    messages: detail.messages.map((message) => (message.id === messageId ? { ...message, suggestions } : message)),
  };
}

/**
 * Asking, following and stopping an answer. The server is the source of
 * truth: a finished answer is written into the cached conversation exactly as
 * it was stored, and only the turn in progress lives here.
 */
export function useChat(conversationId: string | undefined) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [live, setLive] = useState<LiveTurn | null>(null);
  /** For screen readers, through an aria-live region. */
  const [announcement, setAnnouncement] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  /**
   * The open stream already delivered its answer and only the optional tail
   * (suggestions, title) is left: it never blocks a new question.
   */
  const tailRef = useRef(false);
  const reloadTimer = useRef<number | undefined>(undefined);
  const mounted = useRef(true);
  // The conversation on screen now, for callbacks that outlive a render.
  const currentId = useRef(conversationId);
  currentId.current = conversationId;

  useEffect(() => {
    mounted.current = true;
    return () => {
      // Leaving the page (or switching tenant) cancels the answer in progress.
      mounted.current = false;
      controllerRef.current?.abort();
      window.clearTimeout(reloadTimer.current);
    };
  }, []);

  const update = (patch: Partial<LiveTurn>) => setLive((turn) => turn && { ...turn, ...patch });

  /** A first turn that was not stored: its conversation does not exist, back to /chat. */
  function leaveUnsaved(id: string) {
    queryClient.removeQueries({ queryKey: conversationKeys.detail(id) });
    update({ conversationId: null, unsaved: false });
    if (currentId.current === id) navigate("/chat", { replace: true });
  }

  /** Shows what the server kept of a stopped answer. */
  function reloadAfterStop(id: string, lastIdBefore: string | null, unsaved: boolean) {
    reloadTimer.current = window.setTimeout(() => {
      if (!mounted.current) return;
      void queryClient
        .fetchQuery({
          queryKey: conversationKeys.detail(id),
          queryFn: ({ signal }) => getConversation(id, signal),
          staleTime: 0,
        })
        .then((fresh) => {
          // A new last message (the stopped turn, kept as interrupted) replaces
          // the note; so does a stopped retry of an interrupted turn, which the
          // server leaves as it was.
          const last = fresh.messages.at(-1);
          if ((last?.id ?? null) !== lastIdBefore || last?.status === "interrupted") {
            setLive((turn) => (turn?.state === "interrupted" ? null : turn));
          }
        })
        .catch(() => {
          // Not stored (yet): the note stays; a new chat goes back to /chat.
          if (unsaved) leaveUnsaved(id);
        });
      void queryClient.invalidateQueries({ queryKey: conversationKeys.lists() });
    }, RELOAD_AFTER_STOP_MS);
  }

  async function ask(text: string, retry = false): Promise<void> {
    const question = text.trim();
    if (!question) return;
    if (controllerRef.current) {
      if (!tailRef.current) return; // an answer is in progress
      controllerRef.current.abort(); // the previous answer is in: drop its tail
    }
    window.clearTimeout(reloadTimer.current);

    const controller = new AbortController();
    controllerRef.current = controller;
    tailRef.current = false;
    let answered = false;
    let id = conversationId ?? null;
    let unsaved = false;
    const lastIdBefore = id
      ? (queryClient.getQueryData<ConversationDetail>(conversationKeys.detail(id))?.messages.at(-1)?.id ?? null)
      : null;

    setLive({ conversationId: id, question, retry, unsaved, state: "streaming", phase: null, error: null });
    setAnnouncement("Answering…");

    /** The validated answer: shown at once, even if the stream goes on. */
    function showResult(result: QueryResponse) {
      answered = true;
      tailRef.current = true;
      const key = conversationKeys.detail(result.conversation_id ?? id ?? "");
      if (queryClient.getQueryData(key)) {
        queryClient.setQueryData<ConversationDetail>(key, (detail) =>
          detail ? withTurn(detail, question, result, retry) : detail,
        );
      } else {
        void queryClient.invalidateQueries({ queryKey: key });
      }
      setLive(null);
      setAnnouncement("Answer ready");
      void queryClient.invalidateQueries({ queryKey: conversationKeys.lists() });
    }

    try {
      await streamQuery(
        { question, conversation_id: id ?? undefined, retry },
        {
          signal: controller.signal,
          onEvent: (event) => {
            if (event.type === "phase") {
              update({ phase: event.data });
            } else if (event.type === "result") {
              showResult(event.data);
            } else if (event.type === "suggestions" && id) {
              const { message_id, suggestions } = event.data;
              queryClient.setQueryData<ConversationDetail>(conversationKeys.detail(id), (detail) =>
                detail ? withSuggestions(detail, message_id, suggestions) : detail,
              );
            } else if (event.type === "title") {
              const { conversation_id, title } = event.data;
              queryClient.setQueryData<ConversationDetail>(conversationKeys.detail(conversation_id), (detail) =>
                detail ? { ...detail, title } : detail,
              );
              void queryClient.invalidateQueries({ queryKey: conversationKeys.lists() });
            } else if (event.type === "conversation" && !id) {
              // A new chat gets its id: it goes into the URL, without a history entry.
              const newId = event.data.conversation_id;
              id = newId;
              unsaved = true;
              const now = new Date().toISOString();
              queryClient.setQueryData<ConversationDetail>(conversationKeys.detail(newId), {
                id: newId,
                title: event.data.title,
                created_at: now,
                updated_at: now,
                messages: [],
              });
              update({ conversationId: newId, unsaved: true });
              navigate(`/chat/${newId}`, { replace: true });
            }
          },
        },
      );
    } catch (error) {
      if (answered) {
        // Only the optional tail was lost: the answer is already shown.
      } else if (controller.signal.aborted) {
        update({ state: "interrupted" });
        setAnnouncement("Answer stopped");
        if (id) reloadAfterStop(id, lastIdBefore, unsaved);
      } else {
        update({ state: "failed", error: toApiError(error) });
        setAnnouncement("The answer failed");
        if (id && unsaved) leaveUnsaved(id);
      }
    } finally {
      // A newer question may have taken over (it aborted this tail).
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        tailRef.current = false;
      }
      void queryClient.invalidateQueries({ queryKey: conversationKeys.lists() });
    }
  }

  /** The Stop button: only an answer in progress, never the tail of a finished one. */
  function stop() {
    if (!tailRef.current) controllerRef.current?.abort();
  }

  /** "New chat": forgets a failed or stopped turn (never one in progress). */
  function reset() {
    if (controllerRef.current && !tailRef.current) return;
    window.clearTimeout(reloadTimer.current);
    setLive(null);
    setAnnouncement("");
  }

  // A turn belongs to its conversation: opening another one hides it.
  const visible = live && live.conversationId === (conversationId ?? null) ? live : null;

  return {
    live: visible,
    /** While true, the open conversation must not be fetched from the server. */
    unsavedConversation: Boolean(visible?.unsaved),
    streaming: live?.state === "streaming",
    announcement,
    ask,
    stop,
    reset,
  };
}
