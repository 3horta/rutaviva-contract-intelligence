import { useId } from "react";
import type { ReactNode } from "react";
import styles from "./Panel.module.css";

interface PanelProps {
  title?: ReactNode;
  eyebrow?: string;
  actions?: ReactNode;
  /** No inner padding: for tables and viewers that reach the edges. */
  flush?: boolean;
  className?: string;
  children: ReactNode;
}

/** The white card every block of content sits on. */
export function Panel({ title, eyebrow, actions, flush, className, children }: PanelProps) {
  const titleId = useId();
  return (
    <section
      className={[styles.panel, flush && styles.flush, className].filter(Boolean).join(" ")}
      aria-labelledby={title ? titleId : undefined}
    >
      {(title || actions) && (
        <div className={styles.header}>
          <div>
            {eyebrow && <p className={styles.eyebrow}>{eyebrow}</p>}
            {title && (
              <h2 id={titleId} className={styles.title}>
                {title}
              </h2>
            )}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}
