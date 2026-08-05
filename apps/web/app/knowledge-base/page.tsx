import { KnowledgeBaseWorkspace } from "@/features/library/knowledge-base-workspace";
import { parseKnowledgeBaseRouteState } from "@/lib/route-state";

interface KnowledgeBasePageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function KnowledgeBasePage({
  searchParams,
}: KnowledgeBasePageProps) {
  const raw = await searchParams;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(raw)) {
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, item);
    } else if (value !== undefined) {
      params.set(key, value);
    }
  }
  const route = parseKnowledgeBaseRouteState(params);
  return <KnowledgeBaseWorkspace initialRoute={route} />;
}
