"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { ApiError } from "@/lib/http";

import { createSetupOwner, getSetupStatus, verifySetupCode } from "./api";
import { setupOwnerRequestSchema } from "./contracts";

type WizardStep = "loading" | "code" | "owner" | "complete";
type SetupField = "code" | "username" | "display_name" | "password" | "confirm";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Local RAG could not complete setup. Try again.";
}

export function SetupWizard() {
  const [step, setStep] = useState<WizardStep>("loading");
  const [codeIssued, setCodeIssued] = useState(false);
  const [attemptsRemaining, setAttemptsRemaining] = useState(0);
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<SetupField, string>>>(
    {},
  );
  const errorRef = useRef<HTMLDivElement>(null);
  const ownerHeadingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    void getSetupStatus(controller.signal)
      .then((status) => {
        setCodeIssued(status.code_issued);
        setAttemptsRemaining(status.attempts_remaining);
        setStep(status.state === "setup_complete" ? "complete" : "code");
      })
      .catch((caught) => {
        if (controller.signal.aborted) return;
        setError(errorMessage(caught));
        setStep("code");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  useEffect(() => {
    if (step === "owner") ownerHeadingRef.current?.focus();
  }, [step]);

  async function submitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      await verifySetupCode(code);
      setCode("");
      setStep("owner");
    } catch (caught) {
      const message = errorMessage(caught);
      setFieldErrors({ code: message });
      setError(message);
      try {
        const status = await getSetupStatus();
        setCodeIssued(status.code_issued);
        setAttemptsRemaining(status.attempts_remaining);
      } catch {
        // Keep the actionable challenge error when status refresh is unavailable.
      }
    } finally {
      setBusy(false);
    }
  }

  async function submitOwner(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setFieldErrors({});
    if (password !== confirmPassword) {
      const message = "The two password entries do not match.";
      setFieldErrors({ confirm: message });
      setError(message);
      return;
    }
    const parsed = setupOwnerRequestSchema.safeParse({
      username,
      display_name: displayName,
      password,
    });
    if (!parsed.success) {
      const nextErrors: Partial<Record<SetupField, string>> = {};
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (
          field === "username" ||
          field === "display_name" ||
          field === "password"
        ) {
          nextErrors[field] ??= issue.message;
        }
      }
      setFieldErrors(nextErrors);
      setError(parsed.error.issues.map((issue) => issue.message).join(" "));
      return;
    }
    setBusy(true);
    try {
      await createSetupOwner(parsed.data);
      setPassword("");
      setConfirmPassword("");
      setStep("complete");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (step === "loading") {
    return <p role="status">Checking whether Local RAG needs an owner…</p>;
  }

  if (step === "complete") {
    return (
      <section className="setup-step" aria-labelledby="setup-complete-title">
        <p className="setup-step__status">Setup complete</p>
        <h2 id="setup-complete-title">Your owner account is ready</h2>
        <p>
          Sign in with the username and password you created. Local RAG will then
          guide you to upload your first document.
        </p>
        <Link
          className="button-primary setup-step__link"
          href="/login?next=%2Fknowledge-base&setup=complete"
        >
          Continue to sign in
        </Link>
      </section>
    );
  }

  return (
    <>
      <div className="setup-progress" aria-label="Setup progress">
        <span>{step === "code" ? "Step 1 of 2" : "Step 2 of 2"}</span>
        <progress value={step === "code" ? 1 : 2} max={2} />
      </div>
      {error ? (
        <div
          className="setup-error-summary"
          ref={errorRef}
          role="alert"
          tabIndex={-1}
        >
          <h2>Check this step</h2>
          <p>{error}</p>
        </div>
      ) : null}
      {step === "code" ? (
        <form className="upload-form setup-step" onSubmit={submitCode} noValidate>
          <h2>Enter the one-time setup code</h2>
          <p className="field-help" id="setup-code-help">
            Use the code shown by the Local RAG installer. It expires after 15
            minutes and is never placed in a link or browser history.
          </p>
          {!codeIssued ? (
            <p className="setup-guidance" role="status">
              No usable code is active. Return to the Local RAG launcher and choose
              <strong> Issue new setup code</strong>, then come back here.
            </p>
          ) : null}
          <label htmlFor="setup-code">One-time setup code</label>
          <input
            id="setup-code"
            name="setup-code"
            autoComplete="one-time-code"
            aria-describedby={`setup-code-help setup-code-attempts${
              fieldErrors.code ? " setup-code-error" : ""
            }`}
            aria-invalid={Boolean(fieldErrors.code)}
            required
            value={code}
            onChange={(event) => setCode(event.target.value)}
            disabled={busy || !codeIssued}
          />
          <p className="field-help" id="setup-code-attempts">
            {attemptsRemaining} attempt{attemptsRemaining === 1 ? "" : "s"} remaining
          </p>
          {fieldErrors.code ? (
            <p className="inline-error" id="setup-code-error">
              {fieldErrors.code}
            </p>
          ) : null}
          <button
            className="button-primary"
            type="submit"
            disabled={busy || !codeIssued || code.length < 8}
          >
            {busy ? "Checking code…" : "Continue"}
          </button>
        </form>
      ) : (
        <form className="upload-form setup-step" onSubmit={submitOwner} noValidate>
          <h2 id="owner-details-title" ref={ownerHeadingRef} tabIndex={-1}>
            Create your owner account
          </h2>
          <p className="field-help">
            This is the administrator for this PC. Local RAG does not ship with a
            default account or password.
          </p>
          <label htmlFor="owner-username">Username</label>
          <input
            id="owner-username"
            name="username"
            autoComplete="username"
            aria-describedby={`owner-username-help${
              fieldErrors.username ? " owner-username-error" : ""
            }`}
            aria-invalid={Boolean(fieldErrors.username)}
            spellCheck={false}
            required
            value={username}
            onChange={(event) => setUsername(event.target.value.toLowerCase())}
            disabled={busy}
          />
          <p className="field-help" id="owner-username-help">
            Use 3–32 lowercase letters, numbers, periods, underscores, or hyphens.
          </p>
          {fieldErrors.username ? (
            <p className="inline-error" id="owner-username-error">
              {fieldErrors.username}
            </p>
          ) : null}
          <label htmlFor="owner-display-name">Display name</label>
          <input
            id="owner-display-name"
            name="name"
            autoComplete="name"
            aria-describedby={
              fieldErrors.display_name ? "owner-display-name-error" : undefined
            }
            aria-invalid={Boolean(fieldErrors.display_name)}
            required
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            disabled={busy}
          />
          {fieldErrors.display_name ? (
            <p className="inline-error" id="owner-display-name-error">
              {fieldErrors.display_name}
            </p>
          ) : null}
          <label htmlFor="owner-password">Password</label>
          <div className="setup-password-field">
            <input
              id="owner-password"
              name="new-password"
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              aria-describedby={`owner-password-help${
                fieldErrors.password ? " owner-password-error" : ""
              }`}
              aria-invalid={Boolean(fieldErrors.password)}
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={busy}
            />
            <button
              className="button-secondary"
              type="button"
              aria-pressed={showPassword}
              onClick={() => setShowPassword((visible) => !visible)}
              disabled={busy}
            >
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>
          <p className="field-help" id="owner-password-help">
            Use 14–128 characters. A password manager can create and save it for you.
          </p>
          {fieldErrors.password ? (
            <p className="inline-error" id="owner-password-error">
              {fieldErrors.password}
            </p>
          ) : null}
          <label htmlFor="owner-password-confirm">Confirm password</label>
          <input
            id="owner-password-confirm"
            name="new-password-confirmation"
            type={showPassword ? "text" : "password"}
            autoComplete="new-password"
            aria-describedby={
              fieldErrors.confirm ? "owner-password-confirm-error" : undefined
            }
            aria-invalid={Boolean(fieldErrors.confirm)}
            required
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            disabled={busy}
          />
          {fieldErrors.confirm ? (
            <p className="inline-error" id="owner-password-confirm-error">
              {fieldErrors.confirm}
            </p>
          ) : null}
          <button className="button-primary" type="submit" disabled={busy}>
            {busy ? "Creating owner…" : "Create owner account"}
          </button>
        </form>
      )}
    </>
  );
}
