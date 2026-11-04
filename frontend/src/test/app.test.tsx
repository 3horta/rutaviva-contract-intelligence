import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "../layout/AppShell";
import { RouteErrorPage } from "../layout/RouteErrorPage";
import { mockFetch } from "../mocks/server";
import { demoTokens } from "../mocks/tokens";
import { renderApp } from "./renderApp";

afterEach(() => {
  vi.restoreAllMocks();
});

const pdf = (name: string) => new File([new Uint8Array(2_048)], name, { type: "application/pdf" });

describe("signing in", () => {
  it("asks for a token, and shows the dashboard once one is pasted", async () => {
    renderApp({ signedIn: false });
    expect(await screen.findByRole("heading", { name: "Sign in with an access token" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Access token"), { target: { value: demoTokens()[0] } });
    fireEvent.click(screen.getByRole("button", { name: "Use token" }));

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });

  it("asks for a new token when the API answers 401", async () => {
    const unauthorized = async () =>
      new Response(JSON.stringify({ type: "about:blank", title: "Unauthorized", status: 401, detail: "Expired." }), {
        status: 401,
        headers: { "Content-Type": "application/problem+json" },
      });
    renderApp({ fetch: unauthorized });
    // The heading is also an alert (role="alert"), so it is found by its text.
    expect(await screen.findByText("Token invalid or expired")).toBeInTheDocument();
  });
});

describe("documents table", () => {
  it("searches and filters through the URL", async () => {
    const { router } = renderApp({ route: "/documents" });
    expect(await screen.findByText("fuel_supply_agreement_petroruta.pdf")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Search documents"), { target: { value: "tire" } });
    await waitFor(() => expect(router.state.location.search).toContain("q=tire"));
    await waitFor(() => expect(screen.queryByText("fuel_supply_agreement_petroruta.pdf")).not.toBeInTheDocument());
    expect(screen.getByText("tire_supply_agreement_frontera.pdf")).toBeInTheDocument();

    const filters = screen.getByRole("group", { name: "Filter by status" });
    fireEvent.click(within(filters).getByRole("button", { name: /Failed/ }));
    await waitFor(() => expect(router.state.location.search).toContain("status=failed"));
  });
});

describe("upload", () => {
  it("rejects a non-PDF without calling the API, and sends a PDF", async () => {
    const spy = vi.fn(mockFetch);
    const { container } = renderApp({ fetch: spy });
    await screen.findByRole("heading", { name: "Dashboard" });
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    if (!input) throw new Error("no file input");
    const uploads = () => spy.mock.calls.filter(([, init]) => init?.method === "POST");

    fireEvent.change(input, { target: { files: [new File(["hello"], "notes.txt", { type: "text/plain" })] } });
    expect(await screen.findByText("Not a PDF file")).toBeInTheDocument();
    expect(uploads()).toHaveLength(0);

    fireEvent.change(input, { target: { files: [pdf("new_supply_agreement.pdf")] } });
    expect(await screen.findByText("Sent")).toBeInTheDocument();
    expect(uploads()).toHaveLength(1);
  });
});

describe("tenant isolation", () => {
  it("shows none of the previous tenant's documents after switching", async () => {
    renderApp({ route: "/documents" });
    expect(await screen.findByText("fuel_supply_agreement_petroruta.pdf")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Tenant"), { target: { value: "tenant_b" } });

    expect(await screen.findByText("fleet_insurance_policy_centinela.pdf")).toBeInTheDocument();
    expect(screen.queryByText("fuel_supply_agreement_petroruta.pdf")).not.toBeInTheDocument();
  });
});

describe("route errors", () => {
  it("shows the error page inside the shell when a page throws", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    function Broken(): never {
      throw new Error("boom");
    }
    renderApp({
      route: "/broken",
      routes: [
        {
          element: <AppShell />,
          children: [{ errorElement: <RouteErrorPage />, children: [{ path: "broken", element: <Broken /> }] }],
        },
      ],
    });

    expect(await screen.findByRole("heading", { name: "Something went wrong" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
    // The sidebar is still there, and no stack trace is shown.
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.queryByText(/boom/)).not.toBeInTheDocument();
  });
});
