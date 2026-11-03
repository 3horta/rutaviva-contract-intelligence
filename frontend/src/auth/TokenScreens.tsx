import { KeyRound, ShieldAlert } from "lucide-react";
import { Panel } from "../components/ui/Panel";
import { TokenForm } from "./TokenForm";
import styles from "./TokenScreens.module.css";
import type { TenantSession } from "./authStore";

/** First visit in this tab: nothing can be shown without a token. */
export function TokenRequired() {
  return (
    <div className={styles.screen}>
      <Panel className={styles.card}>
        <div className={styles.icon}>
          <KeyRound size={26} aria-hidden="true" />
        </div>
        <h1 className={styles.title}>Sign in with an access token</h1>
        <p className={styles.text}>
          Paste the JWT of the tenant you want to work with. You can add more tenants later and
          switch between them from the top bar.
        </p>
        <TokenForm autoFocus />
      </Panel>
    </div>
  );
}

/** The API answered 401 to the active token: ask for a fresh one for the same tenant. */
export function TokenExpired({ session }: { session: TenantSession }) {
  return (
    <div className={styles.screen}>
      <Panel className={styles.card}>
        <div className={`${styles.icon} ${styles.danger}`}>
          <ShieldAlert size={26} aria-hidden="true" />
        </div>
        <h1 className={styles.title} role="alert">
          Token invalid or expired
        </h1>
        <p className={styles.text}>
          The API rejected the token for <strong>{session.label}</strong>. Paste a new one to
          continue, or switch to another tenant from the top bar.
        </p>
        <TokenForm submitLabel="Replace token" autoFocus />
      </Panel>
    </div>
  );
}
