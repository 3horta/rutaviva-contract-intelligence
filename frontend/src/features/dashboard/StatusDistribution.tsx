import type { DocumentStats } from "../../api/types";
import { formatNumber } from "../../lib/format";
import { DOCUMENT_STATUS, DOCUMENT_STATUSES } from "../documents/status";
import styles from "./Dashboard.module.css";

/**
 * One bar per status, scaled to the largest. Plain markup instead of a chart
 * library: four bars do not need a canvas, and the numbers stay readable text.
 */
export function StatusDistribution({ stats }: { stats: DocumentStats }) {
  const max = Math.max(1, ...DOCUMENT_STATUSES.map((s) => stats.by_status[s]));
  return (
    <ul className={styles.bars}>
      {DOCUMENT_STATUSES.map((status) => {
        const meta = DOCUMENT_STATUS[status];
        const count = stats.by_status[status];
        const share = stats.total ? Math.round((count / stats.total) * 100) : 0;
        return (
          <li key={status} className={styles.barRow}>
            <span className={styles.barLabel}>{meta.label}</span>
            <span className={styles.barTrack} aria-hidden="true">
              <span
                className={`${styles.barFill} ${styles[meta.tone] ?? ""}`}
                style={{ width: `${(count / max) * 100}%` }}
              />
            </span>
            <span className={styles.barValue}>
              {formatNumber(count)} <small>({share}%)</small>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
