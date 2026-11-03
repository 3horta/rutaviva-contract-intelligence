import { QueryClient } from "@tanstack/react-query";
import { authStore } from "../auth/authStore";
import { ApiError } from "./errors";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A 4xx will not change by asking again; a dropped connection might.
      retry: (failureCount, error) =>
        !(error instanceof ApiError && error.status >= 400 && error.status < 500) &&
        failureCount < 2,
      refetchOnWindowFocus: false,
      staleTime: 5_000,
    },
  },
});

// Data never crosses tenants: switching the active token empties the cache.
let activeKey = authStore.getState().activeKey;
authStore.subscribe(() => {
  const next = authStore.getState().activeKey;
  if (next !== activeKey) {
    activeKey = next;
    queryClient.clear();
  }
});
