import { SetupWizard } from "@/features/setup/setup-wizard";

export const dynamic = "force-dynamic";

export default function SetupPage() {
  return (
    <section className="auth-page setup-page" aria-labelledby="setup-title">
      <div className="auth-card setup-card">
        <p className="auth-brand">Local RAG</p>
        <h1 id="setup-title">Set up Local RAG</h1>
        <p className="auth-copy">
          Create the owner account for this PC. Authentication stays enabled even
          though Personal mode is available only on this computer.
        </p>
        <SetupWizard />
      </div>
    </section>
  );
}
