import { z } from "zod";

export const uuidSchema = z
  .string()
  .regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i)
  .transform((value) => value.toLowerCase());

const timestampSchema = z.iso.datetime({ offset: true });
const questionSchema = z
  .string()
  .min(1)
  .refine((value) => Array.from(value).length <= 2_000, {
    message: "Question must contain at most 2,000 Unicode code points.",
  });

export const chatSummarySchema = z
  .object({
    chat_id: uuidSchema,
    title: z.string().min(1).max(255),
    title_is_manual: z.boolean(),
    scope_mode: z.enum(["all_ready", "selected"]),
    scope_version: z.number().int().positive(),
    created_at: timestampSchema,
    updated_at: timestampSchema,
  })
  .strict();

export const historicalSourceSchema = z
  .object({
    label: z.string().regex(/^S[1-8]$/),
    rank: z.number().int().min(1).max(8),
    document_id: uuidSchema.nullable(),
    chunk_id: uuidSchema.nullable(),
    document_id_snapshot: uuidSchema,
    chunk_id_snapshot: uuidSchema,
    filename: z.string().min(1),
    display_name: z.string().min(1),
    logical_path: z.string().min(1),
    page_start: z.number().int().positive(),
    page_end: z.number().int().positive(),
    section: z.string().nullable(),
    source_sha256: z.string().regex(/^[0-9a-f]{64}$/),
    text_sha256: z.string().regex(/^[0-9a-f]{64}$/),
    retrieval_distance: z.number(),
    rerank_score: z.number(),
    source_available: z.boolean(),
  })
  .strict();

export const streamSourceSchema = historicalSourceSchema
  .omit({
    source_sha256: true,
    text_sha256: true,
    retrieval_distance: true,
    rerank_score: true,
  })
  .strict();

const textQuotePageSchema = z
  .object({
    page: z.number().int().positive(),
    kind: z.literal("text_quote"),
    selector: z
      .object({
        exact: z.string().min(1).max(16_000),
        prefix: z.string().max(256),
        suffix: z.string().max(256),
        sha256: z.string().regex(/^[0-9a-f]{64}$/),
      })
      .strict(),
  })
  .strict();

const ocrRegionSchema = z
  .object({
    x: z.number().finite().min(0).max(1),
    y: z.number().finite().min(0).max(1),
    width: z.number().finite().positive().max(1),
    height: z.number().finite().positive().max(1),
  })
  .strict()
  .refine((region) => region.x + region.width <= 1, {
    message: "OCR region exceeds page width.",
  })
  .refine((region) => region.y + region.height <= 1, {
    message: "OCR region exceeds page height.",
  });

const ocrRegionsPageSchema = z
  .object({
    page: z.number().int().positive(),
    kind: z.literal("ocr_regions"),
    regions: z.array(ocrRegionSchema).min(1).max(256),
  })
  .strict();

export const highlightAnchorSchema = z
  .object({
    version: z.literal(1),
    normalization: z.literal("citation-highlight-v1"),
    pages: z
      .array(z.discriminatedUnion("kind", [textQuotePageSchema, ocrRegionsPageSchema]))
      .min(1)
      .max(32),
  })
  .strict()
  .refine(
    (anchor) =>
      new Set(anchor.pages.map((page) => page.page)).size === anchor.pages.length,
    { message: "Highlight pages must be unique." },
  );

export const citationEvidenceSchema = z
  .object({
    label: z.string().regex(/^S[1-8]$/),
    rank: z.number().int().min(1).max(8),
    document_id: uuidSchema,
    display_name: z.string().min(1),
    logical_path: z.string().min(1),
    page_start: z.number().int().positive(),
    page_end: z.number().int().positive(),
    section: z.string().nullable(),
    parse_method: z.enum(["direct", "ocr"]),
    snapshot_text: z.string().min(1).max(100_000),
    highlight_anchor: highlightAnchorSchema,
    source_sha256: z.string().regex(/^[0-9a-f]{64}$/),
    text_sha256: z.string().regex(/^[0-9a-f]{64}$/),
  })
  .strict();

export const chatTurnSchema = z
  .object({
    turn_id: uuidSchema,
    ordinal: z.number().int().positive(),
    question: questionSchema,
    status: z.enum([
      "generating",
      "complete",
      "failed",
      "interrupted",
      "length_limited",
      "citation_failed",
      "access_revoked",
    ]),
    attempt: z.number().int().positive(),
    scope_version: z.number().int().positive(),
    final_answer: z.string().min(1).nullable(),
    partial_answer: z.string().min(1).nullable(),
    insufficient_context: z.boolean(),
    error: z.string().min(1).max(500).nullable(),
    sources: z.array(historicalSourceSchema),
    citations: z.array(historicalSourceSchema),
    citation_ranks: z.array(z.number().int().min(1).max(8)),
    created_at: timestampSchema,
    updated_at: timestampSchema,
    completed_at: timestampSchema.nullable(),
  })
  .strict();

export const chatDetailSchema = chatSummarySchema
  .extend({
    scope_node_ids: z.array(uuidSchema),
    turns: z.array(chatTurnSchema),
    page: z.number().int().positive(),
    limit: z.number().int().min(1).max(100),
    total: z.number().int().nonnegative(),
  })
  .strict();

export const chatScopeSchema = chatSummarySchema
  .extend({
    scope_node_ids: z.array(uuidSchema),
  })
  .strict();

export const chatListSchema = z.array(chatSummarySchema);

const streamEnvelope = {
  chat_id: uuidSchema,
  turn_id: uuidSchema,
  seq: z.number().int().positive(),
};

export const streamStatusPhaseSchema = z.enum([
  "retrieving",
  "reranking",
  "preparing_answer",
  "reasoning",
  "streaming_answer",
  "continuing_answer",
  "validating_citations",
  "repairing_citations",
]);

export const statusEventSchema = z
  .object({
    ...streamEnvelope,
    phase: streamStatusPhaseSchema,
  })
  .strict();

export const sourcesEventSchema = z
  .object({
    ...streamEnvelope,
    sources: z.array(streamSourceSchema).max(8),
  })
  .strict();

export const reasoningStartEventSchema = z
  .object({
    ...streamEnvelope,
  })
  .strict();

export const reasoningDeltaEventSchema = z
  .object({
    ...streamEnvelope,
    text: z.string().min(1),
  })
  .strict();

export const reasoningEndEventSchema = z
  .object({
    ...streamEnvelope,
    truncated: z.boolean(),
  })
  .strict();

export const tokenEventSchema = z
  .object({
    ...streamEnvelope,
    text: z.string(),
  })
  .strict();

export const answerResetEventSchema = z
  .object({
    ...streamEnvelope,
  })
  .strict();

export const finalEventSchema = z
  .object({
    ...streamEnvelope,
    answer: z.string().min(1),
    insufficient_context: z.boolean(),
    citations: z.array(streamSourceSchema).max(8),
  })
  .strict();

export const errorEventSchema = z
  .object({
    ...streamEnvelope,
    code: z.string().min(1),
    message: z.string().min(1),
  })
  .strict();

export type ChatSummary = z.infer<typeof chatSummarySchema>;
export type ChatDetail = z.infer<typeof chatDetailSchema>;
export type ChatScope = z.infer<typeof chatScopeSchema>;
export type ChatTurn = z.infer<typeof chatTurnSchema>;
export type HistoricalSource = z.infer<typeof historicalSourceSchema>;
export type StreamSource = z.infer<typeof streamSourceSchema>;
export type HighlightAnchor = z.infer<typeof highlightAnchorSchema>;
export type CitationEvidence = z.infer<typeof citationEvidenceSchema>;
export type StreamStatusPhase = z.infer<typeof streamStatusPhaseSchema>;
export type StatusEvent = z.infer<typeof statusEventSchema>;
export type SourcesEvent = z.infer<typeof sourcesEventSchema>;
export type ReasoningStartEvent = z.infer<typeof reasoningStartEventSchema>;
export type ReasoningDeltaEvent = z.infer<typeof reasoningDeltaEventSchema>;
export type ReasoningEndEvent = z.infer<typeof reasoningEndEventSchema>;
export type TokenEvent = z.infer<typeof tokenEventSchema>;
export type AnswerResetEvent = z.infer<typeof answerResetEventSchema>;
export type FinalEvent = z.infer<typeof finalEventSchema>;
export type ErrorEvent = z.infer<typeof errorEventSchema>;
