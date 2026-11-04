import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getConversation } from "../../api/conversations";
import { ApiError, toApiError } from "../../api/errors";
import { streamQuery } from "../../api/queryStream";
import type { ConversationDetail, ConversationMessage, PhaseUpdate, QueryResponse } from "../../api/types";
import { conversationKeys } from "./queries";

/** After Stop, the server gets a moment to store the turn if it had finished. */
export const RELOAD_AFTER_STOP_MS = 1_500;

/** The turn on screen that the server has not returned as stored messages yet. */
export interface LiveTurn {
  /** Null until the server announces the conversation of a new chat. */
  conversationId: string | null;
  question: string;
  retry: boolean;
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
    created_at: now,
  };
  return { ...detail, updated_at: now, messages: [...kept, asked, answered] };
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
  const reloadTimer = useRef<number | undefined>(undefined);
  const mounted = useRef(true);

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

  /** Shows what the server kept of a stopped answer. */
  function reloadAfterStop(id: string, lastIdBefore: string | null) {
    reloadTimer.current = window.setTimeout(() => {
      if (!mounted.current) return;
      void queryClient
        .fetchQuery({
          queryKey: conversationKeys.detail(id),
          queryFn: ({ signal }) => getConversation(id, signal),
          staleTime: 0,
        })
        .then((fresh) => {
          // A new last message means the turn was stored: it replaces the note.
          if ((fresh.messages.at(-1)?.id ?? null) !== lastIdBefore) {
            setLive((turn) => (turn?.state === "interrupted" ? null : turn));
          }
        })
        .catch(() => undefined); // the note stays
      void queryClient.invalidateQueries({ queryKey: conversationKeys.lists() });
    }, RELOAD_AFTER_STOP_MS);
  }

  async function ask(text: string, retry = false): Promise<void> {
    const question = text.trim();
    if (!question || controllerRef.current) return;
    window.clearTimeout(reloadTimer.current);

    const controller = new AbortController();
    controllerRef.current = controller;
    let id = conversationId ?? null;
    const lastIdBefore = id
      ? (queryClient.getQueryData<ConversationDetail>(conversationKeys.detail(id))?.messages.at(-1)?.id ?? null)
      : null;

    setLive({ conversationId: id, question, retry, state: "streaming", phase: null, error: null });
    setAnnouncement("Answering…");

    try {
      const result = await streamQuery(
        { question, conversation_id: id ?? undefined, retry },
        {
          signal: controller.signal,
          onEvent: (event) => {
            if (event.type === "phase") {
              update({ phase: event.data });
            } else if (event.type === "conversation" && !id) {
              // A new chat gets its id: it goes into the URL, without a history entry.
              const newId = event.data.conversation_id;
              id = newId;
              const now = new Date().toISOString();
              queryClient.setQueryData<ConversationDetail>(conversationKeys.detail(newId), {
                id: newId,
                title: event.data.title,
                created_at: now,
                updated_at: now,
                messages: [],
              });
              update({ conversationId: newId });
              navigate(`/chat/${newId}`, { replace: true });
              void queryClient.invalidateQueries({ queryKey: conversationKeys.lists() });
            }
          },
        },
      );
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
    } catch (error) {
      if (controller.signal.aborted) {
        update({ state: "interrupted" });
        setAnnouncement("Answer stopped");
        if (id) reloadAfterStop(id, lastIdBefore);
      } else {
        update({ state: "failed", error: toApiError(error) });
        setAnnouncement("The answer failed");
      }
    } finally {
      controllerRef.current = null;
      void queryClient.invalidateQueries({ queryKey: conversationKeys.lists() });
    }
  }

  function stop() {
    controllerRef.current?.abort();
  }

  /** "New chat": forgets a failed or stopped turn (never one in progress). */
  function reset() {
    if (controllerRef.current) return;
    window.clearTimeout(reloadTimer.current);
    setLive(null);
    setAnnouncement("");
  }

  // A turn belongs to its conversation: opening another one hides it.
  const visible = live && live.conversationId === (conversationId ?? null) ? live : null;

  return {
    live: visible,
    streaming: live?.state === "streaming",
    announcement,
    ask,
    stop,
    reset,
  };
}
