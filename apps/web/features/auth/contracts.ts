import { z } from "zod";

export const authRoleSchema = z.enum(["admin", "member"]);
export const authStatusSchema = z.enum([
  "pending_activation",
  "active",
  "disabled",
  "deleted",
]);

export const authUserSchema = z.object({
  id: z.string().uuid(),
  username: z.string(),
  display_name: z.string(),
  role: authRoleSchema,
  status: authStatusSchema,
});

export const authSessionSchema = z.object({
  user: authUserSchema,
  csrf_token: z.string().min(1),
});

export const authMeSchema = z.object({
  user: authUserSchema.nullable(),
  csrf_token: z.string().min(1),
});

export type AuthUser = z.infer<typeof authUserSchema>;
export type AuthSession = z.infer<typeof authSessionSchema>;
export type AuthMe = z.infer<typeof authMeSchema>;

export function hasValidPermanentPasswordLength(value: string): boolean {
  const characters = Array.from(value).length;
  return characters >= 14 && characters <= 128;
}

const permanentPasswordSchema = z.string().refine(
  hasValidPermanentPasswordLength,
  "Permanent password must contain 14-128 Unicode characters.",
);

export const loginRequestSchema = z.object({
  username: z.string().min(1),
  password: z.string().min(1),
});

export const passwordChangeRequestSchema = z.object({
  current_password: z.string().min(1),
  new_password: permanentPasswordSchema,
});

export const activationRequestSchema = z.object({
  code: z.string().min(1),
  password: permanentPasswordSchema,
});
