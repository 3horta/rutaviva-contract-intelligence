import { FilePlus2, FileText, Upload } from "lucide-react";
import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { Link } from "react-router-dom";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Spinner } from "../../components/ui/Feedback";
import { formatBytes } from "../../lib/format";
import { useUploadQueue } from "./uploadQueueContext";
import { describeSummary, isSettled, summarize, UPLOAD_STATE, type UploadItem } from "./uploadQueue";
import styles from "./UploadPanel.module.css";

/** Drop zone and file picker, with the list of the current (or last) batch. */
export function UploadPanel() {
  const { items, busy, start, clear } = useUploadQueue();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (busy) return;
    // Everything goes to the queue: what is not a PDF shows as an error
    // instead of silently disappearing.
    start(Array.from(event.dataTransfer.files));
  }

  const answered = items.filter((item) => isSettled(item.state)).length;
  const summary = !busy && items.length ? describeSummary(summarize(items)) : "";

  return (
    <section
      className={`${styles.panel} ${dragging ? styles.dragging : ""}`}
      aria-labelledby="upload-title"
      onDragOver={(event) => {
        event.preventDefault();
        if (!busy) setDragging(true);
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={onDrop}
    >
      <div className={styles.icon} aria-hidden="true">
        <Upload size={34} />
      </div>
      <h2 id="upload-title" className={styles.title}>
        Drop your contracts here
      </h2>
      <p className={styles.help}>or pick them from your computer. PDF only, up to 20 MB each.</p>

      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        hidden
        onChange={(event) => {
          start(Array.from(event.target.files ?? []));
          // Emptied so picking the same files again starts a new batch.
          event.target.value = "";
        }}
      />
      <Button
        variant="primary"
        disabled={busy}
        onClick={() => input.current?.click()}
        icon={<FilePlus2 size={16} aria-hidden="true" />}
      >
        Select PDF files
      </Button>

      {/* Announced to screen readers as the batch progresses and ends. */}
      <p className="sr-only" aria-live="polite">
        {busy ? `Uploading ${answered} of ${items.length}` : summary && `Batch finished: ${summary}`}
      </p>

      {items.length > 0 && (
        <div className={styles.batch}>
          <div className={styles.batchHeader}>
            <span>
              {busy ? "Uploading" : "Uploaded"} {answered} of {items.length}
              {summary && <span className={styles.summary}> · {summary}</span>}
            </span>
            {busy ? (
              <Spinner size={16} />
            ) : (
              <Button variant="text" size="small" onClick={clear}>
                Clear
              </Button>
            )}
          </div>
          <ul className={styles.list}>
            {items.map((item) => (
              <UploadRow key={item.index} item={item} />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function UploadRow({ item }: { item: UploadItem }) {
  const meta = UPLOAD_STATE[item.state];
  return (
    <li className={styles.row}>
      <FileText size={16} className={styles.fileIcon} aria-hidden="true" />
      {item.documentId ? (
        <Link to={`/documents/${item.documentId}`} className={styles.name} title={item.name}>
          {item.name}
        </Link>
      ) : (
        <span className={styles.name} title={item.name}>
          {item.name}
        </span>
      )}
      <small className={styles.detail}>{item.detail ?? formatBytes(item.size)}</small>
      <Badge tone={meta.tone} live={!isSettled(item.state)}>
        {meta.label}
      </Badge>
    </li>
  );
}
