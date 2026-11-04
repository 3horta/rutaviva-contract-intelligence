import { Navigate, type RouteObject } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { NotFoundPage } from "./layout/NotFoundPage";
import { RouteErrorPage } from "./layout/RouteErrorPage";

/**
 * The app's routes; shared by the browser router and the tests' memory router.
 * The shell is in the main bundle; each page is its own chunk, loaded by the
 * router when its route is first visited (`lazy`).
 */
export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    // Only if the shell itself fails: then there is no sidebar to keep.
    errorElement: <RouteErrorPage standalone />,
    children: [
      {
        // A page that fails (or whose chunk fails to load) shows its error inside the shell.
        errorElement: <RouteErrorPage />,
        children: [
          { index: true, element: <Navigate to="/dashboard" replace /> },
          {
            path: "dashboard",
            lazy: () => import("./features/dashboard/DashboardPage").then((m) => ({ Component: m.DashboardPage })),
          },
          {
            path: "documents",
            lazy: () => import("./features/documents/list/DocumentsPage").then((m) => ({ Component: m.DocumentsPage })),
          },
          {
            // Optional ?page=N&chunk=<id>: opens the viewer on a cited fragment.
            path: "documents/:id",
            lazy: () =>
              import("./features/documents/detail/DocumentDetailPage").then((m) => ({
                Component: m.DocumentDetailPage,
              })),
          },
          {
            // One route for both, so the page stays mounted when a new chat gets its id.
            path: "chat/:conversationId?",
            lazy: () => import("./features/chat/ChatPage").then((m) => ({ Component: m.ChatPage })),
          },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
