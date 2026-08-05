"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { ApiError } from "@/lib/http";

import { activate } from "./api";
import { useAuth } from "./auth-provider";
import { hasValidPermanentPasswordLength } from "./contracts";

interface ActivationFormProps {
  initialCode: string;
}

export function ActivationForm({ initialCode }: ActivationFormProps) {
  const router = useRouter();
  const { setUser } = useAuth();
  const [code, setCode] = useState(initialCode);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password !== confirmation) {
      setError("Passwords do not match.");
      return;
    }
    if (!hasValidPermanentPasswordLength(password)) {
      setError("Your permanent password must contain 14–128 characters.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const session = await activate(code, password);
      setUser(session.user);
      router.replace("/");
      router.refresh();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Activation failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="upload-form" onSubmit={submit} noValidate>
      <label htmlFor="activation-code">One-time activation code</label>
      <input
        id="activation-code"
        name="code"
        autoComplete="one-time-code"
        required
        value={code}
        onChange={(event) => setCode(event.target.value)}
        disabled={busy}
      />
      <p className="field-help">This code can be used once and expires after 30 minutes.</p>
      <label htmlFor="activation-password">Permanent password</label>
      <input
        id="activation-password"
        name="password"
        type="password"
        autoComplete="new-password"
        required
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        disabled={busy}
      />
      <label htmlFor="activation-password-confirm">Confirm permanent password</label>
      <input
        id="activation-password-confirm"
        name="password_confirmation"
        type="password"
        autoComplete="new-password"
        required
        value={confirmation}
        onChange={(event) => setConfirmation(event.target.value)}
        disabled={busy}
      />
      <div className="form-feedback" aria-live="polite">
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
      </div>
      <button className="button-primary" type="submit" disabled={busy}>
        {busy ? "Activating…" : "Set permanent password"}
      </button>
    </form>
  );
}
