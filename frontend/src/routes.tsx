import { Navigate, type RouteObject } from "react-router-dom";
import { ChatPage } from "./features/chat/ChatPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { DocumentDetailPage } from "./features/documents/detail/DocumentDetailPage";
import { DocumentsPage } from "./features/documents/list/DocumentsPage";
import { AppShell } from "./layout/AppShell";
import { NotFoundPage } from "./layout/NotFoundPage";
import { RouteErrorPage } from "./layout/RouteErrorPage";

/** The app's routes; shared by the browser router and the tests' memory router. */
export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    // Only if the shell itself fails: then there is no sidebar to keep.
    errorElement: <RouteErrorPage standalone />,
    children: [
      {
        // A page that fails shows its error inside the shell, so navigation still works.
        errorElement: <RouteErrorPage />,
        children: [
          { index: true, element: <Navigate to="/dashboard" replace /> },
          { path: "dashboard", element: <DashboardPage /> },
          { path: "documents", element: <DocumentsPage /> },
          // Optional ?page=N&chunk=<id>: opens the viewer on a cited fragment.
          { path: "documents/:id", element: <DocumentDetailPage /> },
          // One route for both, so the page stays mounted when a new chat gets its id.
          { path: "chat/:conversationId?", element: <ChatPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
