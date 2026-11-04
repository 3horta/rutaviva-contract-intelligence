import { LogIn, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Button } from "../components/ui/Button";
import { ErrorAlert, LoadingState, Spinner } from "../components/ui/Feedback";
import type { DemoTenant } from "../api/types";
import type { TenantSession } from "./authStore";
import { signInAsDemoTenant, useDemoTenants } from "./demoLogin";
import styles from "./DemoSignIn.module.css";
import { TokenForm } from "./TokenForm";

export const DEMO_NOTICE = "Demo mode: sign-in without a password. Do not deploy with DEMO_LOGIN on.";

interface SignInOptionsProps {
  onSignedIn?: (session: TenantSession) => void;
  /** Label of the token form's submit button. */
  submitLabel?: string;
  autoFocus?: boolean;
}

/**
 * With demo mode on: one button per tenant, and the token form folded below.
 * Off (or the API unreachable): the token form alone, as before.
 */
export function SignInOptions({ onSignedIn, submitLabel, autoFocus }: SignInOptionsProps) {
  const demo = useDemoTenants();

  if (demo.isPending) return <LoadingState compact label="Loading sign-in options…" />;
  const tenants = demo.data;
  if (!tenants) return <TokenForm onAdded={onSignedIn} submitLabel={submitLabel} autoFocus={autoFocus} />;

  return (
    <div className={styles.options}>
      {tenants.length ? (
        <DemoTenantButtons tenants={tenants} onSignedIn={onSignedIn} autoFocus={autoFocus} />
      ) : (
        <p className={styles.empty}>
          No tenants yet. Seed them with <code>make seed</code>.
        </p>
      )}
      <p className={styles.notice}>
        <TriangleAlert size={15} aria-hidden="true" />
        <span>{DEMO_NOTICE}</span>
      </p>
      <details className={styles.advanced}>
        <summary>Use a token (advanced)</summary>
        <TokenForm onAdded={onSignedIn} submitLabel={submitLabel} />
      </details>
    </div>
  );
}

function DemoTenantButtons({
  tenants,
  onSignedIn,
  autoFocus,
}: {
  tenants: DemoTenant[];
  onSignedIn?: (session: TenantSession) => void;
  autoFocus?: boolean;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function signIn(name: string) {
    setPending(name);
    setError(null);
    let session: TenantSession;
    try {
      session = await signInAsDemoTenant(name);
    } catch (caught) {
      setPending(null);
      setError(caught);
      return;
    }
    setPending(null);
    onSignedIn?.(session);
  }

  return (
    <div className={styles.buttons}>
      {tenants.map((tenant, index) => (
        <Button
          key={tenant.name}
          variant={index === 0 ? "primary" : "secondary"}
          autoFocus={autoFocus && index === 0}
          disabled={pending !== null}
          aria-busy={pending === tenant.name || undefined}
          icon={pending === tenant.name ? <Spinner size={16} /> : <LogIn size={16} aria-hidden="true" />}
          onClick={() => void signIn(tenant.name)}
        >
          Sign in as {tenant.label}
        </Button>
      ))}
      {error !== null && <ErrorAlert error={error} title="Could not sign in" />}
    </div>
  );
}

/** Top-bar reminder that anyone can sign in to any tenant while demo mode is on. */
export function DemoModeBadge() {
  const { data } = useDemoTenants();
  if (!data) return null;
  return (
    <span className={styles.badge} title={DEMO_NOTICE}>
      <TriangleAlert size={13} aria-hidden="true" />
      Demo mode
      <span className="sr-only">: sign-in without a password</span>
    </span>
  );
}
