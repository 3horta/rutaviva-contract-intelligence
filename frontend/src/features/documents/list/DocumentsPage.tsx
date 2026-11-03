import { FileSearch, Search, Upload } from "lucide-react";
import { useCallback, useEffect, useId, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { DocumentStatus } from "../../../api/types";
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
import { nextOrdering, type SortField } from "./ordering";
import { PAGE_SIZES, parseTableState, serializeTableState, type TableState } from "./tableParams";

const SEARCH_DEBOUNCE_MS = 300;

export function DocumentsPage() {
  const searchId = useId();
  // The whole table state (page, size, sort, search, status) lives in the URL, so the
  // back button and shared links restore it, and the dashboard cards can link to a status.
  const [params, setParams] = useSearchParams();
  const { page, pageSize, ordering, search, status } = parseTableState(params);

  /** Page and sort changes add a history entry (Back undoes them); typing replaces it. */
  const update = useCallback(
    (patch: Partial<TableState>, options: { replace?: boolean } = {}) =>
      setParams((current) => serializeTableState({ ...parseTableState(current), ...patch }), options),
    [setParams],
  );

  // The input is local while typing; its debounced value is written to the URL.
  const [searchText, setSearchText] = useState(search);
  const debouncedSearch = useDebouncedValue(searchText.trim(), SEARCH_DEBOUNCE_MS);
  useEffect(() => {
    if (debouncedSearch !== search) update({ search: debouncedSearch, page: 0 }, { replace: true });
  }, [debouncedSearch, search, update]);
  // And the other way round: Back/Forward to another search refills the input.
  useEffect(() => {
    setSearchText((text) => (text.trim() === search ? text : search));
  }, [search]);

  const stats = useDocumentStats();
  const list = useDocumentList({
    limit: pageSize,
    offset: page * pageSize,
    ordering,
    search: search || undefined,
    status,
  });

  function filterByStatus(next: DocumentStatus | undefined) {
    update({ status: next, page: 0 });
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
              onChange={(event) => setSearchText(event.target.value)}
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
              onSort={(field: SortField) =>
                update({ ordering: nextOrdering(ordering, field), page: 0 })
              }
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
              onPageChange={(next) => update({ page: next })}
              onPageSizeChange={(size) => update({ pageSize: size, page: 0 }, { replace: true })}
            />
          </>
        ) : null}
      </section>
    </Page>
  );
}
