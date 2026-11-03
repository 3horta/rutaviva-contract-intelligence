import { useSyncExternalStore } from "react";
import { authStore, type AuthState, type TenantSession } from "./authStore";

export interface UseAuth extends AuthState {
  activeSession: TenantSession | null;
}

export function useAuth(): UseAuth {
  const state = useSyncExternalStore(authStore.subscribe, authStore.getState);
  return {
    ...state,
    activeSession: state.sessions.find((s) => s.tenantKey === state.activeKey) ?? null,
  };
}
