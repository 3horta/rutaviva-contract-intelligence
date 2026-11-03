import { ChevronLeft, ChevronRight } from "lucide-react";
import { useId } from "react";
import { formatNumber } from "../../lib/format";
import { Button } from "./Button";
import { Select } from "./Field";
import styles from "./Pagination.module.css";

interface PaginationProps {
  total: number;
  /** Zero-based page. */
  page: number;
  pageSize: number;
  pageSizes: readonly number[];
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}

export function Pagination({
  total,
  page,
  pageSize,
  pageSizes,
  onPageChange,
  onPageSizeChange,
}: PaginationProps) {
  const sizeId = useId();
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const first = total === 0 ? 0 : page * pageSize + 1;
  const last = Math.min(total, (page + 1) * pageSize);

  return (
    <nav className={styles.pagination} aria-label="Pagination">
      <span className={styles.range}>
        {formatNumber(first)}–{formatNumber(last)} of {formatNumber(total)}
      </span>
      <div className={styles.controls}>
        <label htmlFor={sizeId} className={styles.sizeLabel}>
          Rows per page
        </label>
        <Select
          id={sizeId}
          value={pageSize}
          onChange={(event) => onPageSizeChange(Number(event.target.value))}
        >
          {pageSizes.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </Select>
        <Button
          iconOnly
          size="small"
          aria-label="Previous page"
          disabled={page <= 0}
          onClick={() => onPageChange(page - 1)}
          icon={<ChevronLeft size={16} aria-hidden="true" />}
        />
        <span className={styles.page} aria-live="polite">
          Page {page + 1} of {pages}
        </span>
        <Button
          iconOnly
          size="small"
          aria-label="Next page"
          disabled={page + 1 >= pages}
          onClick={() => onPageChange(page + 1)}
          icon={<ChevronRight size={16} aria-hidden="true" />}
        />
      </div>
    </nav>
  );
}
