import { KeyRound } from "lucide-react";
import { useId, useState } from "react";
import type { FormEvent } from "react";
import { Button } from "../components/ui/Button";
import { Field, Textarea } from "../components/ui/Field";
import { USE_MOCKS } from "../config";
import { authStore, type TenantSession } from "./authStore";
import styles from "./TokenForm.module.css";

interface TokenFormProps {
  onAdded?: (session: TenantSession) => void;
  submitLabel?: string;
  autoFocus?: boolean;
}

/** Paste a JWT; it is validated locally (shape, tenant claim, expiry) before use. */
export function TokenForm({ onAdded, submitLabel = "Use token", autoFocus }: TokenFormProps) {
  const id = useId();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    const result = authStore.addToken(value);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setValue("");
    setError(null);
    onAdded?.(result.session);
  }

  async function addDemoTokens() {
    const { demoTokens } = await import("../mocks/tokens");
    let last: TenantSession | null = null;
    // The first one ends up active.
    for (const token of [...demoTokens()].reverse()) {
      const result = authStore.addToken(token);
      if (result.ok) last = result.session;
    }
    if (last) onAdded?.(last);
  }

  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <Field
        label="Access token"
        htmlFor={`${id}-token`}
        hint={
          <>
            Generate one with <code>make token tenant=tenant_a</code>. It is kept only in this
            browser tab.
          </>
        }
        error={error}
        errorId={`${id}-error`}
      >
        <Textarea
          id={`${id}-token`}
          mono
          rows={4}
          value={value}
          autoFocus={autoFocus}
          spellCheck={false}
          autoComplete="off"
          placeholder="eyJhbGciOi…"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          onChange={(event) => setValue(event.target.value)}
        />
      </Field>
      <div className={styles.actions}>
        {USE_MOCKS && (
          <Button variant="text" onClick={() => void addDemoTokens()}>
            Use demo tokens
          </Button>
        )}
        <Button type="submit" variant="primary" icon={<KeyRound size={16} aria-hidden="true" />}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
