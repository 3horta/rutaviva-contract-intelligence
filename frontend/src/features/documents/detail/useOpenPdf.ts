import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { getDocumentFile } from "../../../api/documents";
import { documentKeys } from "../queries";

/** How long the blob URL stays valid after opening: enough for the new tab to load it. */
const REVOKE_AFTER_MS = 60_000;

/**
 * "Open PDF" and "Download": the file needs the auth header, so it is fetched (or taken
 * from the cache the viewer filled) as a blob URL, then opened in a new tab or saved.
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

  /** Fetch the file once (cached) and return a short-lived blob URL for it. */
  const blobUrl = useCallback(async () => {
    const buffer = await queryClient.fetchQuery({
      queryKey: documentKeys.file(documentId),
      queryFn: ({ signal }) => getDocumentFile(documentId, signal),
      staleTime: Infinity,
    });
    const url = URL.createObjectURL(new Blob([buffer], { type: "application/pdf" }));
    urls.current.push(url);
    window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
    return url;
  }, [documentId, queryClient]);

  const run = useCallback(async (action: (url: string) => void) => {
    setPending(true);
    setError(null);
    try {
      action(await blobUrl());
    } catch (caught) {
      setError(caught);
    } finally {
      setPending(false);
    }
  }, [blobUrl]);

  const open = useCallback(
    () =>
      run((url) => {
        const tab = window.open(url, "_blank");
        setBlockedUrl(tab ? null : url);
      }),
    [run],
  );

  /** Save the file under its original name (a temporary link with `download`). */
  const download = useCallback(
    (filename: string) =>
      run((url) => {
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.append(link);
        link.click();
        link.remove();
      }),
    [run],
  );

  return { open, download, pending, error, blockedUrl };
}
