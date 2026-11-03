import { AlertCircle, Info } from "lucide-react";
import type { ReactNode } from "react";
import { toApiError } from "../../api/errors";
import styles from "./Feedback.module.css";

export function Spinner({ size = 18, label }: { size?: number; label?: string }) {
  return (
    <span
      className={styles.spinner}
      style={{ width: size, height: size }}
      role={label ? "status" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}

interface StateBlockProps {
  icon?: ReactNode;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  /** Without its own card, inside a panel or a table. */
  compact?: boolean;
}

/** Centred block for empty, waiting and not-found states. */
export function StateBlock({ icon, title, children, actions, compact }: StateBlockProps) {
  return (
    <div className={[styles.state, compact && styles.compact].filter(Boolean).join(" ")}>
      {icon && <span className={styles.stateIcon}>{icon}</span>}
      {title && <h2 className={styles.stateTitle}>{title}</h2>}
      {children && <p className={styles.stateText}>{children}</p>}
      {actions}
    </div>
  );
}

export function LoadingState({ label, compact }: { label: string; compact?: boolean }) {
  return (
    <div
      className={[styles.state, compact && styles.compact].filter(Boolean).join(" ")}
      role="status"
    >
      <Spinner size={24} />
      <span>{label}</span>
    </div>
  );
}

interface ErrorAlertProps {
  error: unknown;
  title?: string;
  actions?: ReactNode;
}

interface AlertProps {
  tone: "error" | "info";
  title?: ReactNode;
  children: ReactNode;
  /** Small print under the message, such as a trace id or an error code. */
  reference?: ReactNode;
  actions?: ReactNode;
}

export function Alert({ tone, title, children, reference, actions }: AlertProps) {
  const Icon = tone === "error" ? AlertCircle : Info;
  return (
    <div className={`${styles.alert} ${styles[tone]}`} role={tone === "error" ? "alert" : "note"}>
      <Icon size={16} aria-hidden="true" />
      <div className={styles.alertContent}>
        {title && <p className={styles.alertTitle}>{title}</p>}
        <div className={styles.alertBody}>{children}</div>
        {reference && <small className={styles.reference}>{reference}</small>}
        {actions && <div className={styles.alertActions}>{actions}</div>}
      </div>
    </div>
  );
}

/** Shows any failed request the same way: the API's detail, then its trace id. */
export function ErrorAlert({ error, title, actions }: ErrorAlertProps) {
  const apiError = toApiError(error);
  return (
    <Alert
      tone="error"
      title={title}
      actions={actions}
      reference={
        apiError.traceId && (
          <>
            Reference: <code>{apiError.traceId}</code>
          </>
        )
      }
    >
      {apiError.detail}
    </Alert>
  );
}
