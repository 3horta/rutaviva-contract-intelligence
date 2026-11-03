/** The claims the UI reads. The signature is never checked here: the API does it. */
export interface TokenClaims {
  tenant_id?: string;
  tenant_name?: string;
  /** Seconds since the epoch. */
  exp?: number;
}

function decodeBase64Url(segment: string): string {
  const base64 = segment.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

/** Reads the payload of a JWT, or null when the text is not one. */
export function decodeClaims(token: string): TokenClaims | null {
  const parts = token.trim().split(".");
  if (parts.length !== 3 || !parts[1]) return null;
  try {
    const payload: unknown = JSON.parse(decodeBase64Url(parts[1]));
    if (typeof payload !== "object" || payload === null) return null;
    const claims = payload as Record<string, unknown>;
    return {
      tenant_id: typeof claims.tenant_id === "string" ? claims.tenant_id : undefined,
      tenant_name: typeof claims.tenant_name === "string" ? claims.tenant_name : undefined,
      exp: typeof claims.exp === "number" ? claims.exp : undefined,
    };
  } catch {
    return null;
  }
}

export function isExpired(claims: TokenClaims, now = Date.now()): boolean {
  return claims.exp !== undefined && claims.exp * 1000 <= now;
}
