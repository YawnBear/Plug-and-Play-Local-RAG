import type { Route } from "@playwright/test";

export const FOLDER_ALPHA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const FOLDER_BETA = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
export const DOCUMENT = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
export const DOCUMENT_NODE = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
export const TIMESTAMP = "2026-07-23T00:00:00Z";
export const API_ROUTE = /http:\/\/localhost:3100\/api\/.*/;
export const CHAT_ALPHA = "11111111-1111-4111-8111-111111111111";
export const CHAT_BETA = "22222222-2222-4222-8222-222222222222";
export const TURN = "33333333-3333-4333-8333-333333333333";
export const CHAT_DOCUMENT = "44444444-4444-4444-8444-444444444444";
export const CHAT_CHUNK = "55555555-5555-4555-8555-555555555555";
export const CHAT_NODE = "66666666-6666-4666-8666-666666666666";

const corsHeaders = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
};

export async function fulfillJson(
  route: Route,
  body: unknown,
  status = 200,
): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers: corsHeaders,
    body: JSON.stringify(body),
  });
}

export async function fulfillEmpty(
  route: Route,
  status = 204,
): Promise<void> {
  await route.fulfill({ status, headers: corsHeaders });
}

export async function fulfillAuthMeIfRequested(route: Route): Promise<boolean> {
  const request = route.request();
  if (
    request.method() !== "GET" ||
    new URL(request.url()).pathname !== "/api/auth/me"
  ) {
    return false;
  }
  await fulfillJson(route, {
    user: {
      id: "99999999-9999-4999-8999-999999999999",
      username: "testadmin",
      display_name: "Test Admin",
      role: "admin",
      status: "active",
    },
    csrf_token: "deterministic-playwright-csrf",
  });
  return true;
}

export function libraryTree() {
  return [
    {
      node_id: FOLDER_ALPHA,
      parent_id: null,
      name: "Alpha",
      logical_path: "/Alpha",
      children: [],
    },
    {
      node_id: FOLDER_BETA,
      parent_id: null,
      name: "Beta",
      logical_path: "/Beta",
      children: [],
    },
  ];
}

export function betaFolder() {
  return {
    node_id: FOLDER_BETA,
    parent_id: null,
    kind: "folder",
    name: "Beta",
    logical_path: "/Beta",
    document_id: null,
    uploader_user_id: null,
    can_manage: true,
    can_create_children: true,
    readable_document_count: 0,
  };
}

export function betaFile() {
  return {
    node_id: DOCUMENT_NODE,
    parent_id: FOLDER_BETA,
    kind: "file",
    name: "Evidence.pdf",
    logical_path: "/Beta/Evidence.pdf",
    document_id: DOCUMENT,
    uploader_user_id: "99999999-9999-4999-8999-999999999999",
    can_manage: true,
    can_create_children: false,
    readable_document_count: 1,
  };
}

export function documentSummary() {
  return {
    document_id: DOCUMENT,
    filename: "original.pdf",
    sha256: "a".repeat(64),
    state: "ready",
    page_count: 5,
    chunk_count: 12,
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    error: null,
    node_id: DOCUMENT_NODE,
    parent_id: FOLDER_BETA,
    display_name: "Evidence.pdf",
    logical_path: "/Beta/Evidence.pdf",
    uploader_user_id: "99999999-9999-4999-8999-999999999999",
    can_manage: true,
    team_ids: [],
  };
}

export function chatSummary(
  chatId: string,
  title: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    chat_id: chatId,
    title,
    title_is_manual: true,
    scope_mode: "all_ready",
    scope_version: 1,
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    ...overrides,
  };
}

export function streamSource(sourceAvailable = true) {
  return {
    label: "S1",
    rank: 1,
    document_id: sourceAvailable ? CHAT_DOCUMENT : null,
    chunk_id: sourceAvailable ? CHAT_CHUNK : null,
    document_id_snapshot: CHAT_DOCUMENT,
    chunk_id_snapshot: CHAT_CHUNK,
    filename: "original.pdf",
    display_name: "Research.pdf",
    logical_path: "/Beta/Research.pdf",
    page_start: 2,
    page_end: 2,
    section: "Findings",
    source_available: sourceAvailable,
  };
}

export function historicalSource(sourceAvailable = true) {
  return {
    ...streamSource(sourceAvailable),
    source_sha256: "a".repeat(64),
    text_sha256: "b".repeat(64),
    retrieval_distance: 0.1,
    rerank_score: 2.5,
  };
}

export function citationEvidence() {
  return {
    label: "S1",
    rank: 1,
    document_id: CHAT_DOCUMENT,
    display_name: "Research.pdf",
    logical_path: "/Beta/Research.pdf",
    page_start: 2,
    page_end: 2,
    section: "Findings",
    parse_method: "direct",
    snapshot_text: "The complete cited chunk.",
    highlight_anchor: {
      version: 1,
      normalization: "citation-highlight-v1",
      pages: [
        {
          page: 2,
          kind: "text_quote",
          selector: {
            exact: "The complete cited chunk.",
            prefix: "",
            suffix: "",
            sha256:
              "72986ab2e7055fd5e17953e756967cb6ec035bd6b33dc6bdb08824ff8887d972",
          },
        },
      ],
    },
    source_sha256: "a".repeat(64),
    text_sha256: "b".repeat(64),
  };
}

export function digitalCitationPdf(): Buffer {
  const content = "BT /F1 12 Tf 72 720 Td (The complete cited chunk.) Tj ET";
  const objects = [
    "",
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    "<< /Length 0 >>\nstream\n\nendstream",
    `<< /Length ${Buffer.byteLength(content, "ascii")} >>\nstream\n${content}\nendstream`,
  ];
  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  for (let index = 1; index < objects.length; index += 1) {
    offsets[index] = Buffer.byteLength(pdf, "ascii");
    pdf += `${index} 0 obj\n${objects[index]}\nendobj\n`;
  }
  const xrefOffset = Buffer.byteLength(pdf, "ascii");
  pdf += `xref\n0 ${objects.length}\n0000000000 65535 f \n`;
  pdf += offsets
    .slice(1)
    .map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`)
    .join("");
  pdf += `trailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
  return Buffer.from(pdf, "ascii");
}

export function completedTurn({
  status = "complete",
  attempt = 1,
  sourceAvailable = true,
}: {
  status?: "complete" | "failed" | "interrupted" | "generating" | "access_revoked";
  attempt?: number;
  sourceAvailable?: boolean;
} = {}) {
  const complete = status === "complete";
  const source = historicalSource(sourceAvailable);
  return {
    turn_id: TURN,
    ordinal: 1,
    question: "What was found?",
    status,
    attempt,
    scope_version: 1,
    final_answer: complete ? "The verified finding [S1]." : null,
    partial_answer: null,
    insufficient_context: false,
    error: status === "failed" ? "Generation failed." : null,
    sources: [source],
    citations: complete ? [source] : [],
    citation_ranks: complete ? [1] : [],
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    completed_at: complete || status === "interrupted" ? TIMESTAMP : null,
  };
}

export function chatDetail(
  chatId: string,
  title: string,
  turns: unknown[] = [],
  overrides: Record<string, unknown> = {},
) {
  return {
    ...chatSummary(chatId, title),
    scope_node_ids: [] as string[],
    turns,
    page: 1,
    limit: 50,
    total: turns.length,
    ...overrides,
  };
}

export function sseBody(chatId: string, answer = "The verified finding [S1].") {
  const source = streamSource();
  const events = [
    ["status", { phase: "retrieving" }],
    ["status", { phase: "reranking" }],
    ["status", { phase: "preparing_answer" }],
    ["sources", { sources: [source] }],
    ["status", { phase: "reasoning" }],
    ["reasoning_start", {}],
    ["reasoning_delta", { text: "Reviewing the retrieved evidence." }],
    ["reasoning_end", { truncated: false }],
    ["status", { phase: "streaming_answer" }],
    ["token", { text: answer }],
    ["status", { phase: "validating_citations" }],
    [
      "final",
      {
        answer,
        insufficient_context: false,
        citations: [source],
      },
    ],
  ] as const;
  return events
    .map(
      ([name, payload], index) =>
        `event: ${name}\ndata: ${JSON.stringify({
          chat_id: chatId,
          turn_id: TURN,
          seq: index + 1,
          ...payload,
        })}\n\n`,
    )
    .join("");
}
