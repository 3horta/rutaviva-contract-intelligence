import { ChevronDown, History, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { ConversationSummary } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { ErrorAlert, Spinner } from "../../components/ui/Feedback";
import { Input } from "../../components/ui/Field";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { groupConversations } from "./grouping";
import { useConversationList, useDeleteConversation, useRenameConversation } from "./queries";
import styles from "./ConversationSidebar.module.css";

const SEARCH_DEBOUNCE_MS = 250;

/** Pages may overlap when a conversation moves to the top between them. */
function uniqueById(conversations: ConversationSummary[]): ConversationSummary[] {
  return [...new Map(conversations.map((c) => [c.id, c])).values()];
}

interface ConversationSidebarProps {
  activeId: string | undefined;
  /** While an answer is being written, nothing here can be used. */
  locked: boolean;
  onNewChat: () => void;
}

export function ConversationSidebar({ activeId, locked, onNewChat }: ConversationSidebarProps) {
  const bodyId = useId();
  const navigate = useNavigate();
  // On narrow screens the list folds away so the chat has the room.
  const [unfolded, setUnfolded] = useState(false);
  const [searchText, setSearchText] = useState("");
  const search = useDebouncedValue(searchText.trim(), SEARCH_DEBOUNCE_MS);

  const list = useConversationList(search);
  const rename = useRenameConversation();
  const remove = useDeleteConversation();

  const [editing, setEditing] = useState<string | null>(null);
  // Enter saves and then the input loses focus: the ref makes sure it saves once.
  const editingRef = useRef<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const conversations = uniqueById(list.data?.pages.flatMap((page) => page.results) ?? []);
  const groups = groupConversations(conversations);

  function startEditing(id: string) {
    editingRef.current = id;
    setEditing(id);
    setConfirming(null);
  }

  function saveTitle(conversation: ConversationSummary, value: string) {
    if (editingRef.current !== conversation.id) return;
    editingRef.current = null;
    setEditing(null);
    const title = value.trim();
    if (title && title !== conversation.title) rename.mutate({ id: conversation.id, title });
  }

  function cancelEditing() {
    editingRef.current = null;
    setEditing(null);
  }

  function confirmDelete(id: string) {
    setConfirming(null);
    // Leave it first, so the page does not reload a conversation that is going away.
    if (id === activeId) navigate("/chat", { replace: true });
    remove.mutate(id);
  }

  const mutationError = rename.error ?? remove.error;

  return (
    <aside className={`${styles.sidebar} ${unfolded ? styles.unfolded : ""} no-print`} aria-label="Conversations">
      <div className={styles.top}>
        <Button
          variant="primary"
          className={styles.newChat}
          disabled={locked}
          onClick={() => {
            setUnfolded(false);
            onNewChat();
          }}
          icon={<Plus size={16} aria-hidden="true" />}
        >
          New chat
        </Button>
        <Button
          className={styles.fold}
          aria-expanded={unfolded}
          aria-controls={bodyId}
          onClick={() => setUnfolded((open) => !open)}
          icon={<History size={16} aria-hidden="true" />}
        >
          History
          <ChevronDown size={14} aria-hidden="true" className={styles.chevron} />
        </Button>
      </div>

      <div id={bodyId} className={styles.body}>
        <div className={styles.search}>
          <Input
            type="search"
            icon={<Search size={15} aria-hidden="true" />}
            placeholder="Search conversations"
            aria-label="Search conversations"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
          />
          {list.isFetching && !list.isPending && !list.isFetchingNextPage && <Spinner size={14} label="Updating" />}
        </div>

        {list.isError && <ErrorAlert error={list.error} title="Could not load the conversations" />}
        {mutationError != null && <ErrorAlert error={mutationError} title="The change was not saved" />}

        <nav className={styles.items} aria-label="Conversation history">
          {list.isPending && <p className={styles.note}>Loading…</p>}
          {list.isSuccess && conversations.length === 0 && (
            <p className={styles.note}>{search ? "No conversation matches that search." : "No conversations yet."}</p>
          )}

          {groups.map((group) => (
            <section key={group.label} aria-label={group.label}>
              <h3 className={styles.group}>{group.label}</h3>
              <ul className={styles.list}>
                {group.items.map((conversation) => {
                  const active = conversation.id === activeId;
                  return (
                    <li key={conversation.id} className={`${styles.item} ${active ? styles.active : ""}`}>
                      {editing === conversation.id ? (
                        <input
                          className={styles.titleInput}
                          defaultValue={conversation.title}
                          maxLength={120}
                          aria-label="Conversation title"
                          autoFocus
                          onFocus={(event) => event.currentTarget.select()}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") saveTitle(conversation, event.currentTarget.value);
                            if (event.key === "Escape") cancelEditing();
                          }}
                          onBlur={(event) => saveTitle(conversation, event.currentTarget.value)}
                        />
                      ) : confirming === conversation.id ? (
                        <span className={styles.confirm}>
                          Delete this chat?
                          <button type="button" className={styles.confirmYes} onClick={() => confirmDelete(conversation.id)}>
                            Delete
                          </button>
                          <button type="button" onClick={() => setConfirming(null)}>
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <>
                          <Link
                            to={`/chat/${conversation.id}`}
                            className={styles.link}
                            title={conversation.title}
                            aria-current={active ? "page" : undefined}
                            aria-disabled={locked || undefined}
                            tabIndex={locked ? -1 : undefined}
                            onClick={(event) => {
                              if (locked) event.preventDefault();
                              else setUnfolded(false);
                            }}
                          >
                            {conversation.title || "Untitled"}
                          </Link>
                          <button
                            type="button"
                            className={styles.action}
                            aria-label={`Rename "${conversation.title}"`}
                            title="Rename"
                            disabled={locked}
                            onClick={() => startEditing(conversation.id)}
                          >
                            <Pencil size={13} aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            className={styles.action}
                            aria-label={`Delete "${conversation.title}"`}
                            title="Delete"
                            disabled={locked}
                            onClick={() => {
                              cancelEditing();
                              setConfirming(conversation.id);
                            }}
                          >
                            <Trash2 size={13} aria-hidden="true" />
                          </button>
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}

          {list.hasNextPage && (
            <Button
              variant="text"
              size="small"
              className={styles.more}
              disabled={list.isFetchingNextPage}
              onClick={() => void list.fetchNextPage()}
            >
              {list.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          )}
        </nav>
      </div>
    </aside>
  );
}
