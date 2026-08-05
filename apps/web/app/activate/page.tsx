import { ActivationForm } from "@/features/auth/activation-form";

export const dynamic = "force-dynamic";

interface ActivationPageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function ActivationPage({ searchParams }: ActivationPageProps) {
  const raw = await searchParams;
  const code = typeof raw.code === "string" ? raw.code : "";
  return (
    <section className="auth-page" aria-labelledby="activation-title">
      <div className="auth-card">
        <p className="auth-brand">Local RAG</p>
        <h1 id="activation-title">Choose your permanent password</h1>
        <p className="auth-copy">
          Activation is one-time. No temporary password is used.
        </p>
        <ActivationForm initialCode={code} />
      </div>
    </section>
  );
}
