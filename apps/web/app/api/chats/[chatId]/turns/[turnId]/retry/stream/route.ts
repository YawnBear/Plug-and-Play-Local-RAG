import {
  chatStreamIdentifier,
  proxyChatStream,
} from "@/features/chats/server-stream-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(
  request: Request,
  context: { params: Promise<{ chatId: string; turnId: string }> },
): Promise<Response> {
  const { chatId: rawChatId, turnId: rawTurnId } = await context.params;
  const chatId = chatStreamIdentifier(rawChatId, "chat_id");
  if (chatId instanceof Response) return chatId;
  const turnId = chatStreamIdentifier(rawTurnId, "turn_id");
  if (turnId instanceof Response) return turnId;
  return proxyChatStream(
    request,
    `/api/chats/${chatId}/turns/${turnId}/retry/stream`,
  );
}
