/**
 * The anonymous rater id of this browser (ADR-019): a random UUID made up the
 * first time it is needed and kept in localStorage, so each person keeps their
 * own rating of an answer. It is sent as `X-Rater-Id`. It tells browsers apart;
 * it is not a login (the tenant still comes from the token).
 */
export const RATER_HEADER = "X-Rater-Id";

const STORAGE_KEY = "rutaviva.raterId";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Used when storage is blocked: one id for as long as the page lives. */
let inMemory: string | null = null;

/** A version 4 UUID. `crypto.randomUUID` needs a secure context; `getRandomValues` does not. */
function newUuid(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function raterId(): string {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && UUID.test(stored)) return stored;
  } catch {
    // Storage blocked: fall back to the id in memory.
  }
  inMemory ??= newUuid();
  try {
    localStorage.setItem(STORAGE_KEY, inMemory);
  } catch {
    // Storage blocked: the id lasts until the page is reloaded.
  }
  return inMemory;
}

/** Tests only: forget the id kept in memory. */
export function resetRaterIdForTests(): void {
  inMemory = null;
}
