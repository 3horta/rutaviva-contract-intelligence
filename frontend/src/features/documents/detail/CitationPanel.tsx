import { FileText, Quote } from "lucide-react";
import type { UseQueryResult } from "@tanstack/react-query";
import { toApiError } from "../../../api/errors";
import type { ChunkOut } from "../../../api/types";
import { ErrorAlert, LoadingState } from "../../../components/ui/Feedback";
import { Panel } from "../../../components/ui/Panel";
import styles from "./DocumentDetail.module.css";

interface CitationPanelProps {
  filename: string;
  chunk: UseQueryResult<ChunkOut>;
}

/**
 * The cited fragment as text, with its document and page. It never depends on
 * the PDF rendering: if the viewer fails, the citation is still readable.
 */
export function CitationPanel({ filename, chunk }: CitationPanelProps) {
  return (
    <Panel eyebrow="Cited fragment" title="Source of the answer" className={styles.citation}>
      {chunk.isPending ? (
        <LoadingState label="Loading the fragment…" compact />
      ) : chunk.isError ? (
        <ErrorAlert
          error={chunk.error}
          title={
            toApiError(chunk.error).isNotFound
              ? "This fragment no longer exists"
              : "Could not load the fragment"
          }
        />
      ) : (
        <>
          <p className={styles.citationSource}>
            <FileText size={15} aria-hidden="true" />
            <span className={styles.citationFile}>{filename}</span>
            <span className={styles.citationPage}>Page {chunk.data.page}</span>
          </p>
          <blockquote className={styles.quote}>
            <Quote size={16} aria-hidden="true" className={styles.quoteMark} />
            <p>{chunk.data.content}</p>
          </blockquote>
          <p className={styles.citationMeta}>
            Fragment #{chunk.data.chunk_index + 1}
            {chunk.data.bbox
              ? " · highlighted on the page"
              : " · its exact position is not known, so the page is shown without a highlight"}
          </p>
        </>
      )}
    </Panel>
  );
}
