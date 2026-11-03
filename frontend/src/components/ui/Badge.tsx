import type { ReactNode } from "react";
import type { DocumentStatus } from "../../api/types";
import { isTerminal, statusMeta, type Tone } from "../../features/documents/status";
import styles from "./Badge.module.css";

interface BadgeProps {
  tone: Tone;
  children: ReactNode;
  /** Pulsing dot, for states that are still changing. */
  live?: boolean;
  title?: string;
}

export function Badge({ tone, children, live, title }: BadgeProps) {
  const className = [styles.badge, styles[tone], live && styles.live].filter(Boolean).join(" ");
  return (
    <span className={className} title={title}>
      <span className={styles.dot} aria-hidden="true" />
      {children}
    </span>
  );
}

/** A document status, always drawn from the central status map. */
export function StatusBadge({ status, title }: { status: DocumentStatus; title?: string }) {
  const meta = statusMeta(status);
  return (
    <Badge tone={meta.tone} live={!isTerminal(status)} title={title}>
      {meta.label}
    </Badge>
  );
}
