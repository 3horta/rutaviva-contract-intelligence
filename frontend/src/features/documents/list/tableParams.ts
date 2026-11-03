import { DOCUMENT_ORDERINGS, type DocumentOrdering, type DocumentStatus } from "../../../api/types";
import { DOCUMENT_STATUS } from "../status";
import { DEFAULT_ORDERING } from "./ordering";

export const PAGE_SIZES = [10, 25, 50] as const;

/** Everything that defines what the documents table shows. Kept in the URL so the back
 * button and shared links restore it. */
export interface TableState {
  page: number; // 0-based internally; 1-based in the URL (`?page=2` is the 2nd page)
  pageSize: number;
  ordering: DocumentOrdering;
  search: string;
  status: DocumentStatus | undefined;
}

export const DEFAULT_TABLE_STATE: TableState = {
  page: 0,
  pageSize: PAGE_SIZES[0],
  ordering: DEFAULT_ORDERING,
  search: "",
  status: undefined,
};

const isOrdering = (value: string | null): value is DocumentOrdering =>
  value !== null && (DOCUMENT_ORDERINGS as readonly string[]).includes(value);

const isStatus = (value: string | null): value is DocumentStatus =>
  value !== null && Object.hasOwn(DOCUMENT_STATUS, value);

/** Read the state from the URL. Anything missing or invalid falls back to its default,
 * so a hand-edited or stale link never breaks the page. */
export function parseTableState(params: URLSearchParams): TableState {
  const page = Number.parseInt(params.get("page") ?? "", 10);
  const size = Number.parseInt(params.get("size") ?? "", 10);
  const sort = params.get("sort");
  const status = params.get("status");
  return {
    page: Number.isInteger(page) && page >= 1 ? page - 1 : DEFAULT_TABLE_STATE.page,
    pageSize: (PAGE_SIZES as readonly number[]).includes(size) ? size : DEFAULT_TABLE_STATE.pageSize,
    ordering: isOrdering(sort) ? sort : DEFAULT_TABLE_STATE.ordering,
    search: (params.get("q") ?? "").trim(),
    status: isStatus(status) ? status : undefined,
  };
}

/** Write the state to URL params, omitting defaults so URLs stay short. */
export function serializeTableState(state: TableState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.status) params.set("status", state.status);
  if (state.search) params.set("q", state.search);
  if (state.ordering !== DEFAULT_TABLE_STATE.ordering) params.set("sort", state.ordering);
  if (state.pageSize !== DEFAULT_TABLE_STATE.pageSize) params.set("size", String(state.pageSize));
  if (state.page > 0) params.set("page", String(state.page + 1));
  return params;
}
