import type { DocumentOrdering } from "../../../api/types";

export type SortField = "filename" | "status" | "created_at";
export type SortDirection = "ascending" | "descending";

export const DEFAULT_ORDERING: DocumentOrdering = "-created_at";

export function sortOf(ordering: DocumentOrdering): { field: SortField; direction: SortDirection } {
  const descending = ordering.startsWith("-");
  return {
    field: (descending ? ordering.slice(1) : ordering) as SortField,
    direction: descending ? "descending" : "ascending",
  };
}

/**
 * Clicking the sorted column flips it; another column starts in its natural
 * direction (newest first for dates, A to Z for text).
 */
export function nextOrdering(current: DocumentOrdering, field: SortField): DocumentOrdering {
  const { field: currentField, direction } = sortOf(current);
  if (currentField === field) {
    return (direction === "ascending" ? `-${field}` : field) as DocumentOrdering;
  }
  return (field === "created_at" ? `-${field}` : field) as DocumentOrdering;
}
