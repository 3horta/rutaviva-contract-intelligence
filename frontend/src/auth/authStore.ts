import { decodeClaims, isExpired } from "./jwt";

/** One pasted token. The tenant is the identity: a new token for it replaces the old. */
export interface TenantSession {
  tenantKey: string;
  label: string;
  token: string;
}

export interface AuthState {
  sessions: readonly TenantSession[];
  activeKey: string | null;
  /** The API answered 401 to the active token. */
  activeTokenInvalid: boolean;
}

export type AddTokenResult = { ok: true; session: TenantSession } | { ok: false; error: string };

/** sessionStorage: tokens live as long as the tab, never on disk across sessions. */
const STORAGE_KEY = "rutaviva.sessions";

interface StoredState {
  sessions: TenantSession[];
  activeKey: string | null;
}

function load(): AuthState {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw) {
      const stored = JSON.parse(raw) as StoredState;
      if (Array.isArray(stored.sessions)) {
        const activeKey = stored.sessions.some((s) => s.tenantKey === stored.activeKey)
          ? stored.activeKey
          : (stored.sessions[0]?.tenantKey ?? null);
        return { sessions: stored.sessions, activeKey, activeTokenInvalid: false };
      }
    }
  } catch {
    // Storage blocked or corrupted: start empty.
  }
  return { sessions: [], activeKey: null, activeTokenInvalid: false };
}

function persist(state: AuthState): void {
  try {
    const stored: StoredState = { sessions: [...state.sessions], activeKey: state.activeKey };
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
  } catch {
    // Storage blocked: the tokens last until the page is reloaded.
  }
}

function createAuthStore() {
  let state = load();
  const listeners = new Set<() => void>();

  const getActiveSession = (): TenantSession | null =>
    state.sessions.find((s) => s.tenantKey === state.activeKey) ?? null;

  const getActiveToken = (): string | null => getActiveSession()?.token ?? null;

  function set(next: AuthState): void {
    state = next;
    persist(state);
    listeners.forEach((listener) => listener());
  }

  return {
    subscribe(listener: () => void): () => void {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    getState(): AuthState {
      return state;
    },

    getActiveSession,
    getActiveToken,

    /** Validates a pasted token, stores it and makes its tenant the active one. */
    addToken(raw: string): AddTokenResult {
      const token = raw.trim().replace(/^Bearer\s+/i, "");
      const claims = decodeClaims(token);
      if (!claims) return { ok: false, error: "This does not look like a JWT." };
      const tenantKey = claims.tenant_id ?? claims.tenant_name;
      if (!tenantKey) return { ok: false, error: "The token has no tenant claim." };
      if (isExpired(claims)) return { ok: false, error: "This token has already expired." };

      const session: TenantSession = {
        tenantKey,
        label: claims.tenant_name ?? tenantKey,
        token,
      };
      const others = state.sessions.filter((s) => s.tenantKey !== tenantKey);
      set({ sessions: [...others, session], activeKey: tenantKey, activeTokenInvalid: false });
      return { ok: true, session };
    },

    select(tenantKey: string): void {
      if (tenantKey === state.activeKey) return;
      if (!state.sessions.some((s) => s.tenantKey === tenantKey)) return;
      set({ ...state, activeKey: tenantKey, activeTokenInvalid: false });
    },

    remove(tenantKey: string): void {
      const sessions = state.sessions.filter((s) => s.tenantKey !== tenantKey);
      const activeKey =
        state.activeKey === tenantKey ? (sessions[0]?.tenantKey ?? null) : state.activeKey;
      const changed = activeKey !== state.activeKey;
      set({
        sessions,
        activeKey,
        activeTokenInvalid: changed ? false : state.activeTokenInvalid,
      });
    },

    /** Called by the HTTP client on a 401. Ignored if the user switched meanwhile. */
    markInvalid(token: string): void {
      if (token !== getActiveToken() || state.activeTokenInvalid) return;
      set({ ...state, activeTokenInvalid: true });
    },
  };
}

export const authStore = createAuthStore();
