import { createContext, useContext } from "react";
import type { UploadItem } from "./uploadQueue";

export interface UploadQueueValue {
  items: readonly UploadItem[];
  busy: boolean;
  /** Starts a batch. Ignored while another one is running. */
  start: (files: readonly File[]) => void;
  /** Empties the list of the last batch. */
  clear: () => void;
}

export const UploadQueueContext = createContext<UploadQueueValue | null>(null);

export function useUploadQueue(): UploadQueueValue {
  const value = useContext(UploadQueueContext);
  if (!value) throw new Error("useUploadQueue must be used inside UploadQueueProvider");
  return value;
}
