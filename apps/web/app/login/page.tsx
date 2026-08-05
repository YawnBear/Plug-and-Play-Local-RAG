import { LoginForm } from "@/features/auth/login-form";
import { safeNextPath } from "@/lib/http";

export const dynamic = "force-dynamic";

interface LoginPageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const raw = await searchParams;
  const nextPath = safeNextPath(typeof raw.next === "string" ? raw.next : null);
  const expired = raw.reason === "expired";
  const setupComplete = raw.setup === "complete";
  return (
    <section className="auth-page" aria-labelledby="login-title">
      <div className="auth-card">
        <p className="auth-brand">Local RAG</p>
        <h1 id="login-title">Sign in to Local RAG</h1>
        <p className="auth-copy">
          {expired
            ? "Your session expired after 30 minutes of inactivity. Sign in to continue."
            : setupComplete
              ? "Your owner account is ready. Sign in to upload your first document."
            : "Use your account to open grounded document answers and the knowledge base."}
        </p>
        <LoginForm nextPath={nextPath} />
      </div>
    </section>
  );
}
