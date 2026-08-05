const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function normalizeUuid(value: string): string | null {
  const normalized = value.toLowerCase();
  return UUID_PATTERN.test(normalized) ? normalized : null;
}

export function isUuid(value: string): boolean {
  return normalizeUuid(value) !== null;
}

export function requireUuid(value: string, label = "identifier"): string {
  const normalized = normalizeUuid(value);
  if (normalized === null) {
    throw new TypeError(`${label} must be a valid UUID.`);
  }
  return normalized;
}
