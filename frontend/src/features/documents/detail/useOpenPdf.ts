import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { getDocumentFile } from "../../../api/documents";
import { documentKeys } from "../queries";

/** How long the blob URL stays valid after opening: enough for the new tab to load it. */
const REVOKE_AFTER_MS = 60_000;

/**
 * "Open PDF": the file needs the auth header, so it is fetched (or taken from
 * the cache the viewer filled) and opened as a blob URL in a new tab.
 */
export function useOpenPdf(documentId: string) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  /** Set when the browser blocked the new tab: shown as a plain link instead. */
  const [blockedUrl, setBlockedUrl] = useState<string | null>(null);
  const urls = useRef<string[]>([]);

  useEffect(() => {
    const created = urls.current;
    return () => created.forEach((url) => URL.revokeObjectURL(url));
  }, []);

  const open = useCallback(async () => {
    setPending(true);
    setError(null);
    try {
      const buffer = await queryClient.fetchQuery({
        queryKey: documentKeys.file(documentId),
        queryFn: ({ signal }) => getDocumentFile(documentId, signal),
        staleTime: Infinity,
      });
      const url = URL.createObjectURL(new Blob([buffer], { type: "application/pdf" }));
      urls.current.push(url);
      window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
      const tab = window.open(url, "_blank");
      setBlockedUrl(tab ? null : url);
    } catch (caught) {
      setError(caught);
    } finally {
      setPending(false);
    }
  }, [documentId, queryClient]);

  return { open, pending, error, blockedUrl };
}
