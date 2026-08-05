import { ChatWorkspace } from "@/features/chats/chat-workspace";
import { parseHomeRouteState } from "@/lib/route-state";

interface HomePageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function HomePage({ searchParams }: HomePageProps) {
  const raw = await searchParams;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(raw)) {
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, item);
    } else if (value !== undefined) {
      params.set(key, value);
    }
  }
  const route = parseHomeRouteState(params);
  return (
    <ChatWorkspace
      initialChatId={route.chatId}
      invalidChatRoute={route.invalidChat}
    />
  );
}
