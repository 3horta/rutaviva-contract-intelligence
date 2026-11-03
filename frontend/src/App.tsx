import { QueryClientProvider } from "@tanstack/react-query";
import { Compass } from "lucide-react";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { queryClient } from "./api/queryClient";
import { ButtonLink } from "./components/ui/Button";
import { StateBlock } from "./components/ui/Feedback";
import { Page } from "./components/ui/Page";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { DocumentDetailPage } from "./features/documents/detail/DocumentDetailPage";
import { DocumentsPage } from "./features/documents/list/DocumentsPage";
import { AppShell } from "./layout/AppShell";

function NotFoundPage() {
  return (
    <Page label="Not found">
      <StateBlock
        icon={<Compass size={30} aria-hidden="true" />}
        title="Page not found"
        actions={<ButtonLink to="/dashboard" variant="primary">Back to the dashboard</ButtonLink>}
      >
        The address does not match any page.
      </StateBlock>
    </Page>
  );
}

const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: "dashboard", element: <DashboardPage /> },
      { path: "documents", element: <DocumentsPage /> },
      // Optional ?page=N&chunk=<id>: opens the viewer on a cited fragment.
      { path: "documents/:id", element: <DocumentDetailPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
