import { CheckCircle2, Clock, FileText, Loader, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { DocumentStats, DocumentStatus } from "../../api/types";
import { KpiCard, KpiGrid } from "../../components/ui/KpiCard";
import { formatNumber } from "../../lib/format";
import { DOCUMENT_STATUS, DOCUMENT_STATUSES } from "../documents/status";

const STATUS_ICON: Record<DocumentStatus, ReactNode> = {
  pending: <Clock size={18} />,
  processing: <Loader size={18} />,
  ready: <CheckCircle2 size={18} />,
  failed: <XCircle size={18} />,
};

/** Totals over every document of the tenant; each card opens the filtered list. */
export function StatusKpis({ stats }: { stats: DocumentStats }) {
  return (
    <KpiGrid label="Documents by status">
      <KpiCard
        label="Total documents"
        value={formatNumber(stats.total)}
        help="Uploaded contracts"
        icon={<FileText size={18} />}
        to="/documents"
      />
      {DOCUMENT_STATUSES.map((status) => {
        const meta = DOCUMENT_STATUS[status];
        return (
          <KpiCard
            key={status}
            label={meta.label}
            value={formatNumber(stats.by_status[status])}
            help={meta.description}
            tone={meta.tone}
            icon={STATUS_ICON[status]}
            to={`/documents?status=${status}`}
          />
        );
      })}
    </KpiGrid>
  );
}
