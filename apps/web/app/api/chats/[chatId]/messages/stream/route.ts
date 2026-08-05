import {
  chatStreamIdentifier,
  proxyChatStream,
} from "@/features/chats/server-stream-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(
  request: Request,
  context: { params: Promise<{ chatId: string }> },
): Promise<Response> {
  const { chatId: rawChatId } = await context.params;
  const chatId = chatStreamIdentifier(rawChatId, "chat_id");
  if (chatId instanceof Response) return chatId;
  return proxyChatStream(request, `/api/chats/${chatId}/messages/stream`);
}
