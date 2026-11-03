import { FileSearch, Search, Upload } from "lucide-react";
import { useId, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { DocumentOrdering, DocumentStatus } from "../../../api/types";
import { ButtonLink } from "../../../components/ui/Button";
import { ErrorAlert, LoadingState, Spinner, StateBlock } from "../../../components/ui/Feedback";
import { Input } from "../../../components/ui/Field";
import { Page, PageHeader } from "../../../components/ui/Page";
import { Pagination } from "../../../components/ui/Pagination";
import { formatNumber } from "../../../lib/format";
import { useDebouncedValue } from "../../../lib/useDebouncedValue";
import { useDocumentList, useDocumentStats } from "../queries";
import { DOCUMENT_STATUS, DOCUMENT_STATUSES } from "../status";
import { DocumentsTable } from "./DocumentsTable";
import styles from "./DocumentsList.module.css";
import { DEFAULT_ORDERING, nextOrdering, type SortField } from "./ordering";

const PAGE_SIZES = [10, 25, 50] as const;
const SEARCH_DEBOUNCE_MS = 300;

const isStatus = (value: string | null): value is DocumentStatus =>
  value !== null && Object.hasOwn(DOCUMENT_STATUS, value);

export function DocumentsPage() {
  const searchId = useId();
  // The status filter lives in the URL, so the dashboard cards can link to it.
  const [params, setParams] = useSearchParams();
  const statusParam = params.get("status");
  const status = isStatus(statusParam) ? statusParam : undefined;

  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<number>(PAGE_SIZES[0]);
  const [ordering, setOrdering] = useState<DocumentOrdering>(DEFAULT_ORDERING);
  const [searchText, setSearchText] = useState("");
  const search = useDebouncedValue(searchText.trim(), SEARCH_DEBOUNCE_MS);

  const stats = useDocumentStats();
  const list = useDocumentList({
    limit: pageSize,
    offset: page * pageSize,
    ordering,
    search: search || undefined,
    status,
  });

  function filterByStatus(next: DocumentStatus | undefined) {
    setPage(0);
    setParams(next ? { status: next } : {}, { replace: true });
  }

  const filtered = Boolean(status || search);

  return (
    <Page label="Documents">
      <PageHeader
        eyebrow="Contract library"
        title="Documents"
        actions={
          <ButtonLink to="/dashboard" variant="primary" icon={<Upload size={16} aria-hidden="true" />}>
            Upload documents
          </ButtonLink>
        }
      />

      <section className={styles.panel} aria-label="Document list">
        <div className={styles.toolbar}>
          <div className={styles.filters} role="group" aria-label="Filter by status">
            <button
              type="button"
              className={styles.filter}
              aria-pressed={!status}
              onClick={() => filterByStatus(undefined)}
            >
              All <span>{formatNumber(stats.data?.total)}</span>
            </button>
            {DOCUMENT_STATUSES.map((s) => (
              <button
                key={s}
                type="button"
                className={styles.filter}
                aria-pressed={status === s}
                onClick={() => filterByStatus(s)}
              >
                {DOCUMENT_STATUS[s].label} <span>{formatNumber(stats.data?.by_status[s])}</span>
              </button>
            ))}
          </div>
          <div className={styles.search}>
            <label htmlFor={searchId} className="sr-only">
              Search documents
            </label>
            <Input
              id={searchId}
              type="search"
              icon={<Search size={15} aria-hidden="true" />}
              placeholder="Search by file name"
              value={searchText}
              onChange={(event) => {
                setSearchText(event.target.value);
                setPage(0);
              }}
            />
            {list.isFetching && !list.isPending && <Spinner size={14} label="Updating" />}
          </div>
        </div>

        {list.isError && (
          <div className={styles.errorWrap}>
            <ErrorAlert error={list.error} title="Could not load the documents" />
          </div>
        )}

        {list.isPending ? (
          <LoadingState label="Loading documents…" compact />
        ) : list.data ? (
          <>
            <DocumentsTable
              documents={list.data.results}
              ordering={ordering}
              onSort={(field: SortField) => {
                setOrdering((current) => nextOrdering(current, field));
                setPage(0);
              }}
              empty={
                <StateBlock
                  compact
                  icon={<FileSearch size={28} aria-hidden="true" />}
                  title={filtered ? "No matching documents" : "No documents yet"}
                >
                  {filtered
                    ? "Try another search or status."
                    : "Upload supplier contracts from the dashboard to see them here."}
                </StateBlock>
              }
            />
            <Pagination
              total={list.data.count}
              page={page}
              pageSize={pageSize}
              pageSizes={PAGE_SIZES}
              onPageChange={setPage}
              onPageSizeChange={(size) => {
                setPageSize(size);
                setPage(0);
              }}
            />
          </>
        ) : null}
      </section>
    </Page>
  );
}
