import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { uploadDocument } from "../../api/documents";
import { documentKeys } from "../documents/queries";
import { UploadQueueContext } from "./uploadQueueContext";
import { isSettled, precheck, runUploads, type UploadItem } from "./uploadQueue";

/**
 * Lives above the routes, so a batch keeps going when the user navigates and
 * its list is still there on return. It is mounted per tenant (see AppShell):
 * switching tenant unmounts it, which aborts whatever was not sent yet.
 */
export function UploadQueueProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [items, setItems] = useState<readonly UploadItem[]>([]);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);

  const busy = items.some((item) => !isSettled(item.state));

  const start = useCallback(
    (files: readonly File[]) => {
      if (!files.length || controller.current) return;
      const batch = precheck(files);
      setItems(batch);

      const abort = new AbortController();
      controller.current = abort;
      void runUploads(batch, files, {
        upload: uploadDocument,
        signal: abort.signal,
        onChange: (index, patch) =>
          setItems((current) =>
            current.map((item) => (item.index === index ? { ...item, ...patch } : item)),
          ),
      }).finally(() => {
        if (controller.current === abort) controller.current = null;
        if (abort.signal.aborted) return;
        void queryClient.invalidateQueries({ queryKey: ["documents", "list"] });
        void queryClient.invalidateQueries({ queryKey: documentKeys.stats() });
      });
    },
    [queryClient],
  );

  const clear = useCallback(() => {
    if (!controller.current) setItems([]);
  }, []);

  const value = useMemo(() => ({ items, busy, start, clear }), [items, busy, start, clear]);
  return <UploadQueueContext.Provider value={value}>{children}</UploadQueueContext.Provider>;
}
