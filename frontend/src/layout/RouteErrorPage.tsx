import { Compass, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect } from "react";
import { isRouteErrorResponse, useRouteError } from "react-router-dom";
import { Button, ButtonLink } from "../components/ui/Button";
import { StateBlock } from "../components/ui/Feedback";
import { Page } from "../components/ui/Page";
import styles from "./RouteErrorPage.module.css";

/**
 * What a route shows when it fails to render or load. A missing route is a
 * "not found"; anything else is a bug, shown without its stack trace (that
 * goes to the console, and only in development).
 */
export function RouteErrorPage({ standalone = false }: { standalone?: boolean }) {
  const error = useRouteError();
  const notFound = isRouteErrorResponse(error) && error.status === 404;

  useEffect(() => {
    if (import.meta.env.DEV) console.error(error);
  }, [error]);

  const actions = (
    <span className={styles.actions}>
      <ButtonLink to="/dashboard" variant="primary">
        Back to the dashboard
      </ButtonLink>
      <Button onClick={() => window.location.reload()} icon={<RefreshCw size={15} aria-hidden="true" />}>
        Reload
      </Button>
    </span>
  );

  const page = (
    <Page label={notFound ? "Not found" : "Error"}>
      {notFound ? (
        <StateBlock icon={<Compass size={30} aria-hidden="true" />} title="Page not found" actions={actions}>
          The address does not match any page.
        </StateBlock>
      ) : (
        <StateBlock
          icon={<TriangleAlert size={30} aria-hidden="true" />}
          title="Something went wrong"
          actions={actions}
        >
          This page could not be shown. Reload it, or go back to the dashboard.
        </StateBlock>
      )}
    </Page>
  );
  return standalone ? <main className={styles.standalone}>{page}</main> : page;
}
