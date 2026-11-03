import {
  Clock,
  Cpu,
  ExternalLink,
  FileSearch,
  Hash,
  Layers,
  Loader,
  Sigma,
} from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { toApiError } from "../../../api/errors";
import type { DocumentOut } from "../../../api/types";
import { StatusBadge } from "../../../components/ui/Badge";
import { Button, ButtonLink } from "../../../components/ui/Button";
import { Alert, ErrorAlert, LoadingState, StateBlock } from "../../../components/ui/Feedback";
import { KpiCard, KpiGrid } from "../../../components/ui/KpiCard";
import { BackLink, Page, PageHeader } from "../../../components/ui/Page";
import { Panel } from "../../../components/ui/Panel";
import { formatDateTime, formatDuration, formatNumber } from "../../../lib/format";
import { useChunk, useDocument, useDocumentFile } from "../queries";
import { failureReason, isTerminal, statusMeta } from "../status";
import { CitationPanel } from "./CitationPanel";
import styles from "./DocumentDetail.module.css";
import { useOpenPdf } from "./useOpenPdf";

// PDF.js is heavy: loaded only when a page of the document is to be shown.
const PdfViewer = lazy(() => import("../viewer/PdfViewer"));

function positiveInt(value: string | null): number | null {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

export function DocumentDetailPage() {
  const { id = "" } = useParams();
  const query = useDocument(id);

  if (query.data) return <DocumentDetail key={query.data.id} doc={query.data} />;

  if (query.isError) {
    const notFound = toApiError(query.error).isNotFound;
    return (
      <Page label="Document">
        <BackLink to="/documents">Documents</BackLink>
        {notFound ? (
          <StateBlock
            icon={<FileSearch size={30} aria-hidden="true" />}
            title="Document not found"
            actions={<ButtonLink to="/documents">Back to documents</ButtonLink>}
          >
            It does not exist, or it belongs to another tenant.
          </StateBlock>
        ) : (
          <ErrorAlert error={query.error} title="Could not load the document" />
        )}
      </Page>
    );
  }

  return (
    <Page label="Document">
      <BackLink to="/documents">Documents</BackLink>
      <LoadingState label="Loading document…" />
    </Page>
  );
}

function DocumentDetail({ doc }: { doc: DocumentOut }) {
  const [params] = useSearchParams();
  const chunkId = params.get("chunk");
  const pageParam = positiveInt(params.get("page"));
  const showSource = chunkId !== null || pageParam !== null;

  const chunk = useChunk(doc.id, chunkId);
  const file = useDocumentFile(doc.id, showSource);
  const openPdf = useOpenPdf(doc.id);

  // The viewer starts where the link points and follows the cited chunk once known.
  const [viewerPage, setViewerPage] = useState(pageParam ?? 1);
  const chunkPage = chunk.data?.page;
  useEffect(() => {
    if (chunkPage) setViewerPage(chunkPage);
  }, [chunkPage]);

  const highlight =
    chunk.data?.bbox != null
      ? { page: chunk.data.page, bbox: chunk.data.bbox, pageWidth: chunk.data.page_width }
      : null;

  const metrics = doc.metrics;
  const processing = !isTerminal(doc.status);

  return (
    <Page label={`Document ${doc.filename}`}>
      <BackLink to="/documents">Documents</BackLink>
      <PageHeader
        eyebrow="Document detail"
        title={doc.filename}
        subtitle={doc.id}
        actions={
          <>
            <StatusBadge status={doc.status} />
            <Button
              onClick={() => void openPdf.open()}
              disabled={openPdf.pending}
              icon={
                openPdf.pending ? (
                  <Loader size={16} className={styles.spin} aria-hidden="true" />
                ) : (
                  <ExternalLink size={16} aria-hidden="true" />
                )
              }
            >
              Open PDF
            </Button>
          </>
        }
      />

      {openPdf.error != null && <ErrorAlert error={openPdf.error} title="Could not open the PDF" />}
      {openPdf.blockedUrl && (
        <Alert tone="info" title="The browser blocked the new tab">
          <a href={openPdf.blockedUrl} target="_blank" rel="noreferrer">
            Open the PDF
          </a>
        </Alert>
      )}

      {/* Announces the end of processing to screen readers. */}
      <p className="sr-only" aria-live="polite">
        Status: {statusMeta(doc.status).label}
      </p>

      {processing && (
        <div className={styles.waiting}>
          <StateBlock icon={<Loader size={28} className={styles.spin} aria-hidden="true" />} title="Processing document">
            This page updates by itself when the document is ready.
          </StateBlock>
        </div>
      )}

      {doc.status === "failed" && (
        <Alert
          tone="error"
          title="Processing failed"
          reference={
            doc.failure_code && (
              <>
                Code: <code>{doc.failure_code}</code>
              </>
            )
          }
        >
          <p className={styles.failureReason}>{failureReason(doc.failure_code)}</p>
          {doc.failure_detail && <p className={styles.failureDetail}>{doc.failure_detail}</p>}
        </Alert>
      )}

      <KpiGrid label="Processing metrics">
        <KpiCard label="Pages" value={formatNumber(doc.page_count)} icon={<Layers size={18} />} />
        <KpiCard
          label="Chunks"
          value={formatNumber(doc.chunk_count)}
          help="Indexed fragments"
          icon={<Hash size={18} />}
          tone="processing"
        />
        <KpiCard
          label="Processing time"
          value={formatDuration(metrics?.processing_ms)}
          icon={<Clock size={18} />}
          tone="pending"
        />
        <KpiCard
          label="Embedding tokens"
          value={formatNumber(metrics?.embedding_tokens)}
          help={metrics?.tokens_estimated ? "Estimated: the provider did not report usage" : "As reported by the provider"}
          icon={<Sigma size={18} />}
          tone={metrics?.tokens_estimated ? "warning" : "success"}
        />
        <KpiCard
          label="Embedding calls"
          value={formatNumber(metrics?.embedding_calls)}
          icon={<Cpu size={18} />}
          tone="neutral"
        />
      </KpiGrid>

      <Panel title="Details">
        <dl className={styles.dataGrid}>
          <div>
            <dt>Embedding model</dt>
            <dd>{doc.embedding_model ?? "—"}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>{statusMeta(doc.status).label}</dd>
          </div>
          <div>
            <dt>Uploaded</dt>
            <dd>{formatDateTime(doc.created_at)}</dd>
          </div>
          <div>
            <dt>Last update</dt>
            <dd>{formatDateTime(doc.updated_at)}</dd>
          </div>
        </dl>
      </Panel>

      {showSource && (
        <div className={styles.source}>
          {chunkId !== null && <CitationPanel filename={doc.filename} chunk={chunk} />}
          <div className={styles.viewerFrame}>
            {file.isError ? (
              <div className={styles.viewerMessage}>
                <ErrorAlert error={file.error} title="Could not load the PDF" />
              </div>
            ) : file.data ? (
              <Suspense fallback={<LoadingState label="Loading viewer…" compact />}>
                <PdfViewer
                  data={file.data}
                  page={viewerPage}
                  onPageChange={setViewerPage}
                  highlight={highlight}
                  label={`${doc.filename}, page viewer`}
                />
              </Suspense>
            ) : (
              <LoadingState label="Downloading the PDF…" compact />
            )}
          </div>
        </div>
      )}
    </Page>
  );
}
