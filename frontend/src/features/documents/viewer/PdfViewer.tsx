import { ChevronLeft, ChevronRight } from "lucide-react";
import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy } from "pdfjs-dist";
// Bundled by Vite and served from our own origin: no CDN, no eval.
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useEffect, useMemo, useRef, useState } from "react";
import type { BBox } from "../../../api/types";
import { Spinner } from "../../../components/ui/Feedback";
import { scaleBBox } from "./bbox";
import styles from "./PdfViewer.module.css";

GlobalWorkerOptions.workerSrc = workerUrl;

export interface PdfHighlight {
  page: number;
  bbox: BBox;
  /** Width in PDF points of the page the bbox was measured on; null: the page's own width. */
  pageWidth: number | null;
}

interface PdfViewerProps {
  data: ArrayBuffer;
  /** 1-based page to show. */
  page: number;
  onPageChange: (page: number) => void;
  highlight: PdfHighlight | null;
  label: string;
}

/** What is on the canvas right now: the highlight is drawn only over this. */
interface Painted {
  page: number;
  cssWidth: number;
  /** The page's width in PDF points, as PDF.js reads it. */
  pointsWidth: number;
}

export default function PdfViewer({ data, page, onPageChange, highlight, label }: PdfViewerProps) {
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [stageWidth, setStageWidth] = useState(0);
  const [painted, setPainted] = useState<Painted | null>(null);

  const pageCount = pdf?.numPages ?? 0;
  const currentPage = pageCount ? Math.min(Math.max(1, page), pageCount) : page;

  // Load the document. PDF.js takes ownership of the buffer it is given, so
  // it gets a copy: the cached original stays usable for "Open PDF".
  useEffect(() => {
    const task = getDocument({ data: new Uint8Array(data.slice(0)), isEvalSupported: false });
    setPdf(null);
    setLoadError(false);
    task.promise.then(setPdf, () => setLoadError(true));
    return () => void task.destroy();
  }, [data]);

  // Fit to the available width, following the layout as it resizes.
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setStageWidth(Math.floor(entry.contentRect.width));
    });
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  // Paint the page. A newer request (page, width or document change) cancels
  // the one in flight, so a slow page never overwrites the current one.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!pdf || !canvas || !stageWidth) return;

    let cancelled = false;
    let cancelRender: (() => void) | null = null;

    void (async () => {
      try {
        const pdfPage = await pdf.getPage(currentPage);
        if (cancelled) return;

        const pointsWidth = pdfPage.getViewport({ scale: 1 }).width;
        const scale = stageWidth / pointsWidth;
        const density = window.devicePixelRatio || 1;
        const viewport = pdfPage.getViewport({ scale: scale * density });
        const context = canvas.getContext("2d");
        if (!context) return;

        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${viewport.width / density}px`;
        canvas.style.height = `${viewport.height / density}px`;

        const task = pdfPage.render({ canvasContext: context, viewport });
        cancelRender = () => task.cancel();
        await task.promise;
        if (!cancelled) setPainted({ page: currentPage, cssWidth: viewport.width / density, pointsWidth });
      } catch {
        // Cancelled by a newer render, or the document was destroyed.
      }
    })();

    return () => {
      cancelled = true;
      cancelRender?.();
    };
  }, [pdf, currentPage, stageWidth]);

  const rect = useMemo(() => {
    if (!highlight || !painted || painted.page !== highlight.page) return null;
    return scaleBBox(highlight.bbox, highlight.pageWidth ?? painted.pointsWidth, painted.cssWidth);
  }, [highlight, painted]);

  // Bring the highlight into view by scrolling the viewer only, never the page.
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage || !painted) return;
    stage.scrollTop = rect ? Math.max(0, rect.top - stage.clientHeight / 3) : 0;
  }, [rect, painted]);

  const goTo = (target: number) => {
    if (target >= 1 && target <= pageCount) onPageChange(target);
  };

  return (
    <div className={styles.viewer} role="region" aria-label={label}>
      <div className={styles.bar}>
        <button
          type="button"
          aria-label="Previous page"
          disabled={currentPage <= 1}
          onClick={() => goTo(currentPage - 1)}
        >
          <ChevronLeft size={18} aria-hidden="true" />
        </button>
        <span aria-live="polite">
          Page {currentPage} of {pageCount || "–"}
        </span>
        <button
          type="button"
          aria-label="Next page"
          disabled={!pageCount || currentPage >= pageCount}
          onClick={() => goTo(currentPage + 1)}
        >
          <ChevronRight size={18} aria-hidden="true" />
        </button>
      </div>

      <div ref={stageRef} className={styles.stage} tabIndex={0} aria-label="PDF page">
        {loadError ? (
          <p className={styles.message}>The PDF could not be displayed.</p>
        ) : (
          !painted && (
            <p className={styles.message}>
              <Spinner size={18} /> Rendering page…
            </p>
          )
        )}
        <div className={styles.page}>
          <canvas ref={canvasRef} />
          {rect && (
            <div
              className={styles.highlight}
              style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
              aria-hidden="true"
            />
          )}
        </div>
      </div>
    </div>
  );
}
