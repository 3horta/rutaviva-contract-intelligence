import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { setMockTimeScale } from "../mocks/chat";
import { renderApp } from "./renderApp";

afterEach(() => setMockTimeScale(0));

async function ask(question: string) {
  const box = await screen.findByLabelText(/^Question/);
  fireEvent.change(box, { target: { value: question } });
  fireEvent.keyDown(box, { key: "Enter" });
}

/** The answers on screen (questions are not articles). */
const answers = () => screen.queryAllByRole("article", { name: "Answer" });

describe("chat", () => {
  it("answers with a confidence badge and citations that open the cited page", async () => {
    const { router } = renderApp({ route: "/chat" });
    await ask("What is the payment term in the fuel supply agreement?");

    const sources = await screen.findByRole("region", { name: "Sources" });
    const answer = answers().at(-1);
    if (!answer) throw new Error("no answer");
    expect(within(answer).getByText(/confidence/i)).toBeInTheDocument();
    // A new chat gets its id in the URL.
    expect(router.state.location.pathname).toMatch(/^\/chat\/[0-9a-f-]+$/);

    const chip = within(sources).getAllByRole("link")[0];
    if (!chip) throw new Error("no citation");
    expect(chip).toHaveTextContent(/fuel_.*\.pdf, p\. \d+/);
    fireEvent.click(chip);
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/documents\/[0-9a-f-]+$/));
    expect(router.state.location.search).toMatch(/page=\d+&chunk=[0-9a-f-]+/);
  });

  it("shows its own state when the documents do not support an answer", async () => {
    renderApp({ route: "/chat" });
    await ask("What is the weather forecast for Laredo next week?");
    expect(await screen.findByText("Not enough evidence")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the warnings of an answer", async () => {
    renderApp({ route: "/chat" });
    await ask("What insurance must suppliers carry?");
    const warnings = await screen.findByRole("list", { name: "Warnings" });
    expect(warnings).toHaveTextContent(/combines clauses from \d contracts/);
  });

  it("stops an answer and says so", async () => {
    renderApp({ route: "/chat" });
    setMockTimeScale(1); // real phase durations, so there is time to stop
    await ask("What is the payment term in the fuel supply agreement?");
    fireEvent.click(await screen.findByRole("button", { name: "Stop the answer" }));

    expect(await screen.findByText(/Answer stopped\. The server may still finish it/)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^Question/)).toBeEnabled();
  });

  it("offers Retry when the answer service times out (504)", async () => {
    const { router } = renderApp({ route: "/chat" });
    await ask("Will this timeout for the fuel agreement?");
    expect(await screen.findByText("The answer service is not available right now")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    // A first turn that failed was not stored: its conversation does not exist.
    expect(router.state.location.pathname).toBe("/chat");
    expect(screen.queryByText("This conversation no longer exists")).not.toBeInTheDocument();
  });

  it("forgets the previous tenant's conversations after switching", async () => {
    renderApp({ route: "/chat" });
    const history = await screen.findByRole("navigation", { name: "Conversation history" });
    expect(await within(history).findByText("How does the tire supply agreement renew?")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Tenant"), { target: { value: "tenant_b" } });

    expect(await screen.findByText("No conversations yet.")).toBeInTheDocument();
    expect(screen.queryByText("How does the tire supply agreement renew?")).not.toBeInTheDocument();
  });
});
