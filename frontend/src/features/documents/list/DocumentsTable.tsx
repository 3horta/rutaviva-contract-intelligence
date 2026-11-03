import { ArrowDown, ArrowUp, ArrowUpDown, ChevronRight, FileText } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { DocumentOrdering, DocumentOut } from "../../../api/types";
import { StatusBadge } from "../../../components/ui/Badge";
import { formatDateTime, formatNumber } from "../../../lib/format";
import { failureReason } from "../status";
import { sortOf, type SortField } from "./ordering";
import styles from "./DocumentsList.module.css";

interface DocumentsTableProps {
  documents: readonly DocumentOut[];
  ordering: DocumentOrdering;
  onSort: (field: SortField) => void;
  empty: ReactNode;
}

interface SortableHeaderProps {
  field: SortField;
  ordering: DocumentOrdering;
  onSort: (field: SortField) => void;
  children: ReactNode;
}

function SortableHeader({ field, ordering, onSort, children }: SortableHeaderProps) {
  const sort = sortOf(ordering);
  const active = sort.field === field;
  const Icon = !active ? ArrowUpDown : sort.direction === "ascending" ? ArrowUp : ArrowDown;
  return (
    <th scope="col" aria-sort={active ? sort.direction : "none"}>
      <button type="button" className={styles.sortButton} onClick={() => onSort(field)}>
        {children}
        <Icon size={13} aria-hidden="true" className={active ? styles.sortActive : undefined} />
      </button>
    </th>
  );
}

export function DocumentsTable({ documents, ordering, onSort, empty }: DocumentsTableProps) {
  const sortProps = { ordering, onSort };

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <SortableHeader field="filename" {...sortProps}>Document</SortableHeader>
            <SortableHeader field="status" {...sortProps}>Status</SortableHeader>
            <th scope="col" className={styles.numeric}>
              Pages
            </th>
            <th scope="col" className={styles.numeric}>
              Chunks
            </th>
            <SortableHeader field="created_at" {...sortProps}>Uploaded</SortableHeader>
            <th scope="col">
              <span className="sr-only">Open</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {documents.length === 0 ? (
            <tr>
              <td colSpan={6}>{empty}</td>
            </tr>
          ) : (
            documents.map((doc) => (
              <tr key={doc.id}>
                <td>
                  <Link to={`/documents/${doc.id}`} className={styles.docName}>
                    <FileText size={20} aria-hidden="true" />
                    <span>
                      <span className={styles.docTitle}>{doc.filename}</span>
                      <small>{doc.id}</small>
                    </span>
                  </Link>
                </td>
                <td>
                  <StatusBadge
                    status={doc.status}
                    title={doc.status === "failed" ? failureReason(doc.failure_code) : undefined}
                  />
                </td>
                <td className={styles.numeric}>{formatNumber(doc.page_count)}</td>
                <td className={styles.numeric}>{formatNumber(doc.chunk_count)}</td>
                <td>
                  <time dateTime={doc.created_at}>{formatDateTime(doc.created_at)}</time>
                </td>
                <td className={styles.open}>
                  <Link
                    to={`/documents/${doc.id}`}
                    className={styles.openLink}
                    aria-label={`Open ${doc.filename}`}
                    tabIndex={-1}
                  >
                    <ChevronRight size={18} aria-hidden="true" />
                  </Link>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
