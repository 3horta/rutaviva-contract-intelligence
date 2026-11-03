import { Building2, LogOut, Plus, X } from "lucide-react";
import { useId, useRef } from "react";
import { Button } from "../components/ui/Button";
import { Select } from "../components/ui/Field";
import { authStore } from "./authStore";
import { TokenForm } from "./TokenForm";
import styles from "./TenantSwitcher.module.css";
import { useAuth } from "./useAuth";

/**
 * Keeps several tokens, one per tenant, labelled with the token's tenant name.
 * Switching empties the query cache (see api/queryClient.ts).
 */
export function TenantSwitcher() {
  const { sessions, activeKey, activeSession } = useAuth();
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const selectId = useId();

  if (!sessions.length) return null;

  return (
    <div className={styles.switcher}>
      <Building2 size={16} className={styles.icon} aria-hidden="true" />
      <label htmlFor={selectId} className="sr-only">
        Tenant
      </label>
      <Select
        id={selectId}
        className={styles.select}
        value={activeKey ?? ""}
        onChange={(event) => authStore.select(event.target.value)}
      >
        {sessions.map((session) => (
          <option key={session.tenantKey} value={session.tenantKey}>
            {session.label}
          </option>
        ))}
      </Select>
      <Button
        iconOnly
        size="small"
        variant="text"
        aria-label="Add a token for another tenant"
        title="Add token"
        onClick={() => dialog.current?.showModal()}
        icon={<Plus size={16} aria-hidden="true" />}
      />
      {activeSession && (
        <Button
          iconOnly
          size="small"
          variant="text"
          aria-label={`Forget the token of ${activeSession.label}`}
          title="Forget this token"
          onClick={() => authStore.remove(activeSession.tenantKey)}
          icon={<LogOut size={16} aria-hidden="true" />}
        />
      )}

      <dialog ref={dialog} className={styles.dialog} aria-labelledby={titleId}>
        <div className={styles.dialogHeader}>
          <h2 id={titleId} className={styles.dialogTitle}>
            Add a tenant token
          </h2>
          <Button
            iconOnly
            size="small"
            variant="text"
            aria-label="Close"
            onClick={() => dialog.current?.close()}
            icon={<X size={16} aria-hidden="true" />}
          />
        </div>
        <TokenForm onAdded={() => dialog.current?.close()} submitLabel="Add and switch" />
      </dialog>
    </div>
  );
}
