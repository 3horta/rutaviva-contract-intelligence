import { ChevronRight, FileText, List } from "lucide-react";
import { Link } from "react-router-dom";
import { StatusBadge } from "../../components/ui/Badge";
import { ButtonLink } from "../../components/ui/Button";
import { ErrorAlert, LoadingState, StateBlock } from "../../components/ui/Feedback";
import { Page, PageHeader } from "../../components/ui/Page";
import { Panel } from "../../components/ui/Panel";
import { formatDateTime } from "../../lib/format";
import { useDocumentList, useDocumentStats } from "../documents/queries";
import { UploadPanel } from "../upload/UploadPanel";
import styles from "./Dashboard.module.css";
import { StatusDistribution } from "./StatusDistribution";
import { StatusKpis } from "./StatusKpis";

const RECENT = { limit: 5, offset: 0, ordering: "-created_at" } as const;

export function DashboardPage() {
  const stats = useDocumentStats();
  const recent = useDocumentList(RECENT);

  return (
    <Page label="Dashboard">
      <PageHeader
        eyebrow="Overview"
        title="Dashboard"
        actions={
          <ButtonLink to="/documents" icon={<List size={16} aria-hidden="true" />}>
            View documents
          </ButtonLink>
        }
      />

      {stats.isError && <ErrorAlert error={stats.error} title="Could not load the totals" />}
      {stats.data && <StatusKpis stats={stats.data} />}

      <div className={styles.main}>
        <UploadPanel />
        <Panel eyebrow="Distribution" title="Documents by status" className={styles.aside}>
          {stats.data ? (
            <StatusDistribution stats={stats.data} />
          ) : stats.isPending ? (
            <LoadingState label="Loading totals…" compact />
          ) : null}
        </Panel>
      </div>

      <Panel
        title="Latest uploads"
        actions={
          <Link to="/documents" className={styles.textLink}>
            View all
          </Link>
        }
      >
        {recent.data && recent.data.results.length === 0 ? (
          <StateBlock compact icon={<FileText size={28} aria-hidden="true" />} title="No documents yet">
            Upload your first supplier contract above to start asking questions about it.
          </StateBlock>
        ) : recent.data ? (
          <ul className={styles.recent}>
            {recent.data.results.map((doc) => (
              <li key={doc.id}>
                <Link to={`/documents/${doc.id}`} className={styles.recentRow}>
                  <span className={styles.recentFile}>
                    <FileText size={16} aria-hidden="true" />
                    <span>{doc.filename}</span>
                  </span>
                  <StatusBadge status={doc.status} />
                  <time dateTime={doc.created_at}>{formatDateTime(doc.created_at)}</time>
                  <ChevronRight size={16} aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ul>
        ) : recent.isError ? (
          <ErrorAlert error={recent.error} />
        ) : (
          <LoadingState label="Loading documents…" compact />
        )}
      </Panel>
    </Page>
  );
}
