import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Tone } from "../../features/documents/status";
import styles from "./KpiCard.module.css";

interface KpiCardProps {
  label: string;
  value: ReactNode;
  help?: ReactNode;
  icon: ReactNode;
  tone?: Tone | "primary";
  /** Makes the whole card a link (e.g. to the list filtered by that status). */
  to?: string;
}

export function KpiCard({ label, value, help, icon, tone = "primary", to }: KpiCardProps) {
  const body = (
    <>
      <span className={styles.icon} aria-hidden="true">
        {icon}
      </span>
      <span>
        <span className={styles.label}>{label}</span>
        <strong className={styles.value}>{value}</strong>
        {help && <small className={styles.help}>{help}</small>}
      </span>
    </>
  );
  const className = `${styles.card} ${styles[tone] ?? ""}`;
  return to ? (
    <Link to={to} className={className}>
      {body}
    </Link>
  ) : (
    <article className={className}>{body}</article>
  );
}

export function KpiGrid({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className={styles.grid} role="group" aria-label={label}>
      {children}
    </div>
  );
}
