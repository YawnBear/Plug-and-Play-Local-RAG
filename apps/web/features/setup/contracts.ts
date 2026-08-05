import { z } from "zod";

export const setupStatusSchema = z
  .object({
    state: z.enum(["setup_required", "setup_complete"]),
    code_issued: z.boolean(),
    code_expires_at: z.string().datetime().nullable(),
    attempts_remaining: z.number().int().min(0).max(5),
  })
  .strict();

export const setupChallengeSchema = z
  .object({
    state: z.literal("owner_details_required"),
    expires_at: z.string().datetime(),
  })
  .strict();

export const setupOwnerResponseSchema = z
  .object({
    state: z.literal("setup_complete"),
    login_path: z.literal("/login"),
    first_document_path: z.literal("/knowledge-base"),
  })
  .strict();

const ownerUsernameSchema = z
  .string()
  .regex(
    /^[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]$/u,
    "Use 3–32 lowercase letters, numbers, periods, underscores, or hyphens.",
  );

const displayNameSchema = z
  .string()
  .trim()
  .min(1, "Enter the name you want Local RAG to display.")
  .max(80, "Display name must be 80 characters or fewer.");

const passwordSchema = z.string().refine(
  (value) => {
    const characters = Array.from(value).length;
    return characters >= 14 && characters <= 128;
  },
  "Use a password containing 14–128 characters.",
);

export const setupOwnerRequestSchema = z.object({
  username: ownerUsernameSchema,
  display_name: displayNameSchema,
  password: passwordSchema,
});

export type SetupStatus = z.infer<typeof setupStatusSchema>;
export type SetupOwnerResponse = z.infer<typeof setupOwnerResponseSchema>;
