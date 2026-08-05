import { normalizeUuid, requireUuid } from "./uuid";

type SearchInput = string | URLSearchParams;

function searchParams(input: SearchInput): URLSearchParams {
  return typeof input === "string"
    ? new URLSearchParams(input.startsWith("?") ? input.slice(1) : input)
    : new URLSearchParams(input);
}

export interface HomeRouteState {
  chatId: string | null;
  invalidChat: boolean;
}

export function parseHomeRouteState(input: SearchInput): HomeRouteState {
  const raw = searchParams(input).get("chat");
  if (raw === null) return { chatId: null, invalidChat: false };
  const chatId = normalizeUuid(raw);
  return { chatId, invalidChat: chatId === null };
}

export function homeRoute(chatId?: string | null): string {
  return chatId ? `/?chat=${encodeURIComponent(requireUuid(chatId, "chat"))}` : "/";
}

export interface KnowledgeBaseRouteState {
  folderId: string | null;
  documentId: string | null;
  page: number | null;
  invalidFolder: boolean;
  invalidDocument: boolean;
  invalidPage: boolean;
}

function positiveInteger(value: string | null): number | null {
  if (value === null || !/^[1-9]\d*$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

export function parseKnowledgeBaseRouteState(
  input: SearchInput,
): KnowledgeBaseRouteState {
  const params = searchParams(input);
  const rawFolder = params.get("folder");
  const rawDocument = params.get("document");
  const rawPage = params.get("page");
  const folderId = rawFolder === null ? null : normalizeUuid(rawFolder);
  const documentId =
    rawDocument === null ? null : normalizeUuid(rawDocument);
  const parsedPage = positiveInteger(rawPage);

  return {
    folderId,
    documentId,
    page: documentId === null ? null : (parsedPage ?? 1),
    invalidFolder: rawFolder !== null && folderId === null,
    invalidDocument: rawDocument !== null && documentId === null,
    invalidPage:
      documentId !== null && rawPage !== null && parsedPage === null,
  };
}

export interface KnowledgeBaseRoute {
  folderId?: string | null;
  documentId?: string | null;
  page?: number | null;
}

export function knowledgeBaseRoute(route: KnowledgeBaseRoute = {}): string {
  const params = new URLSearchParams();
  if (route.folderId) {
    params.set("folder", requireUuid(route.folderId, "folder"));
  }
  if (route.documentId) {
    params.set("document", requireUuid(route.documentId, "document"));
    const page = route.page ?? 1;
    if (!Number.isSafeInteger(page) || page < 1) {
      throw new TypeError("page must be a positive integer.");
    }
    params.set("page", String(page));
  }
  const query = params.toString();
  return query ? `/knowledge-base?${query}` : "/knowledge-base";
}

export function citationRoute(source: {
  document_id: string | null;
  page_start: number;
}): string | null {
  if (!source.document_id) return null;
  return knowledgeBaseRoute({
    documentId: source.document_id,
    page: source.page_start,
  });
}
