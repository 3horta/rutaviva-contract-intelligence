import { FileDown, MessageSquareText, MessagesSquare, Printer } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toApiError } from "../../api/errors";
import { ButtonLink, Button } from "../../components/ui/Button";
import { ErrorAlert, LoadingState, Spinner, StateBlock } from "../../components/ui/Feedback";
import { Page } from "../../components/ui/Page";
import { Composer } from "./Composer";
import { ConversationSidebar } from "./ConversationSidebar";
import { exportConversationPdf } from "./exportPdf";
import { AssistantMessage, FailedAnswer, InterruptedNote, PendingAnswer, UserMessage } from "./Message";
import { useConversation } from "./queries";
import { useChat } from "./useChat";
import styles from "./Chat.module.css";

/** Closer than this to the bottom, the thread follows new content. */
const FOLLOW_THRESHOLD_PX = 80;

/** Starting points for an empty chat; generic, so they fit any tenant's contracts. */
const EXAMPLE_QUESTIONS = [
  "What are the payment terms and late-payment interest?",
  "How and when can each agreement be renewed or terminated?",
  "What insurance must suppliers carry?",
];

/** /chat starts a conversation; /chat/:conversationId opens a stored one. */
export function ChatPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const conversation = useConversation(conversationId);
  const chat = useChat(conversationId);
  const { live, streaming } = chat;

  const threadRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const following = useRef(true);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<unknown>(null);

  const stored = conversation.data?.messages ?? [];
  // A retry replaces the last turn: hide it while the new answer is written.
  const messages = live?.retry && live.state === "streaming" ? stored.slice(0, -2) : stored;
  const lastAnswerIndex = messages.at(-1)?.role === "assistant" ? messages.length - 1 : -1;
  const title = conversation.data?.title || "New chat";

  // Follows the thread as it grows, unless the user scrolled up to read.
  useLayoutEffect(() => {
    const thread = threadRef.current;
    if (thread && following.current) thread.scrollTop = thread.scrollHeight;
  }, [messages, live]);

  // When an answer ends, the box is ready for the next question.
  const wasStreaming = useRef(false);
  useEffect(() => {
    if (wasStreaming.current && !streaming) inputRef.current?.focus();
    wasStreaming.current = streaming;
  }, [streaming]);

  function ask(question: string, retry = false) {
    following.current = true; // a new question always goes to the bottom
    void chat.ask(question, retry);
  }

  function retryLast() {
    const question = messages[lastAnswerIndex - 1];
    if (question?.role === "user") ask(question.content, true);
  }

  async function exportPdf() {
    setExporting(true);
    setExportError(null);
    try {
      await exportConversationPdf(title, stored);
    } catch (error) {
      setExportError(error);
    } finally {
      setExporting(false);
    }
  }

  let body;
  if (conversationId && conversation.isPending) {
    body = <LoadingState label="Loading the conversation…" compact />;
  } else if (conversation.isError) {
    body = toApiError(conversation.error).isNotFound ? (
      <StateBlock
        compact
        icon={<MessagesSquare size={30} aria-hidden="true" />}
        title="This conversation no longer exists"
        actions={<ButtonLink to="/chat">Start a new chat</ButtonLink>}
      >
        It may have been deleted, or it belongs to another tenant.
      </StateBlock>
    ) : (
      <ErrorAlert error={conversation.error} title="Could not load the conversation" />
    );
  } else if (!messages.length && !live) {
    body = (
      <StateBlock
        compact
        icon={<MessageSquareText size={32} aria-hidden="true" />}
        title="Ask about your contracts"
        actions={
          <div className={styles.examples} role="group" aria-label="Example questions">
            {EXAMPLE_QUESTIONS.map((question) => (
              <button key={question} type="button" className={styles.example} onClick={() => ask(question)}>
                {question}
              </button>
            ))}
          </div>
        }
      >
        Every answer cites the clause and page it comes from. Follow-up questions keep the context of the
        conversation.
      </StateBlock>
    );
  } else {
    body = (
      <>
        {messages.map((message, index) =>
          message.role === "user" ? (
            <UserMessage key={message.id} content={message.content} />
          ) : (
            <AssistantMessage
              key={message.id}
              message={message}
              locked={streaming}
              onRetry={index === lastAnswerIndex ? retryLast : undefined}
            />
          ),
        )}
        {live && (
          <>
            <UserMessage content={live.question} />
            {live.state === "streaming" && <PendingAnswer phase={live.phase} />}
            {live.state === "interrupted" && <InterruptedNote />}
            {live.state === "failed" && live.error && (
              <FailedAnswer error={live.error} onRetry={() => ask(live.question)} />
            )}
          </>
        )}
      </>
    );
  }

  const hasMessages = stored.length > 0;

  return (
    <Page label="Chat">
      <div className={styles.layout}>
        <ConversationSidebar
          activeId={conversationId}
          locked={streaming}
          onNewChat={() => {
            chat.reset();
            navigate("/chat");
            inputRef.current?.focus();
          }}
        />

        <section className={styles.chat} aria-label="Conversation">
          <header className={styles.header}>
            <h1 className={styles.title} title={title}>
              {title}
            </h1>
            {hasMessages && (
              <div className={`${styles.headerActions} no-print`}>
                <Button
                  variant="text"
                  iconOnly
                  aria-label="Export to PDF"
                  title="Export to PDF"
                  disabled={exporting || streaming}
                  onClick={() => void exportPdf()}
                  icon={exporting ? <Spinner size={16} /> : <FileDown size={17} aria-hidden="true" />}
                />
                <Button
                  variant="text"
                  iconOnly
                  aria-label="Print"
                  title="Print"
                  disabled={streaming}
                  onClick={() => window.print()}
                  icon={<Printer size={17} aria-hidden="true" />}
                />
              </div>
            )}
          </header>

          {exportError != null && (
            <div className={styles.banner}>
              <ErrorAlert error={exportError} title="Could not export the PDF" />
            </div>
          )}

          <div
            ref={threadRef}
            className={styles.thread}
            onScroll={(event) => {
              const thread = event.currentTarget;
              following.current =
                thread.scrollHeight - thread.scrollTop - thread.clientHeight < FOLLOW_THRESHOLD_PX;
            }}
          >
            {body}
          </div>

          <p className="sr-only" aria-live="polite">
            {chat.announcement}
          </p>

          <Composer inputRef={inputRef} streaming={streaming} onSend={ask} onStop={chat.stop} />
        </section>
      </div>
    </Page>
  );
}
