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
    // The API sends a code; the label is shown.
    expect(warnings).toHaveTextContent("The cited documents give different values; check which one applies.");
  });

  it("marks a fragment excluded as a possible prompt injection", async () => {
    renderApp({ route: "/chat" });
    await ask("Do the fuel contracts contain instructions about payment terms?");
    const warnings = await screen.findByRole("list", { name: "Warnings" });
    expect(warnings).toHaveTextContent("A passage that looked like instructions to the assistant was ignored.");
    expect(screen.getByText("Excluded")).toBeInTheDocument();
  });

  it("stops an answer, keeps the question as interrupted and asks it again", async () => {
    const { router } = renderApp({ route: "/chat" });
    setMockTimeScale(1); // real phase durations, so there is time to stop
    const question = "What is the payment term in the fuel supply agreement?";
    await ask(question);
    fireEvent.click(await screen.findByRole("button", { name: "Stop the answer" }));

    expect(await screen.findByText(/Answer stopped\. The question is kept/)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^Question/)).toBeEnabled();

    // Reloaded from the server: the stored turn, with its own note and action.
    const interrupted = await screen.findByRole("article", { name: "Interrupted answer" }, { timeout: 4_000 });
    expect(within(interrupted).getByText(/^Interrupted/)).toBeInTheDocument();
    expect(router.state.location.pathname).toMatch(/^\/chat\/[0-9a-f-]+$/);
    expect(answers()).toHaveLength(0); // not an answer

    setMockTimeScale(0);
    fireEvent.click(within(interrupted).getByRole("button", { name: "Ask again" }));
    await screen.findByRole("region", { name: "Sources" });
    // The retry replaced the interrupted turn.
    expect(screen.queryByRole("article", { name: "Interrupted answer" })).not.toBeInTheDocument();
    expect(answers()).toHaveLength(1);
  }, 10_000);

  it("suggests follow-ups under the last answer and asks one", async () => {
    renderApp({ route: "/chat" });
    await ask("What is the payment term in the fuel supply agreement?");
    const suggested = await screen.findByRole("region", { name: "Suggested questions" });
    const chips = within(suggested).getAllByRole("button");
    expect(chips.length).toBeGreaterThan(0);
    const next = chips[0]?.textContent ?? "";

    fireEvent.click(chips[0] as HTMLElement);
    await waitFor(() => expect(answers()).toHaveLength(2));
    expect(screen.getAllByText(next).length).toBeGreaterThan(0); // asked as the next question
    // Only one set of suggestions: under the last answer.
    await screen.findByRole("region", { name: "Suggested questions" });
    expect(screen.getAllByRole("region", { name: "Suggested questions" })).toHaveLength(1);
  });

  it("shows the generated title of a new conversation", async () => {
    renderApp({ route: "/chat" });
    await ask("What is the payment term in the fuel supply agreement?");
    expect(await screen.findByRole("heading", { level: 1, name: "Payment term in the fuel supply agreement" })).toBeInTheDocument();
    const history = screen.getByRole("navigation", { name: "Conversation history" });
    expect(await within(history).findByText("Payment term in the fuel supply agreement")).toBeInTheDocument();
  });

  it("shows stored suggestions after opening a conversation", async () => {
    renderApp({ route: "/chat" });
    const history = await screen.findByRole("navigation", { name: "Conversation history" });
    fireEvent.click(await within(history).findByText("How does the tire supply agreement renew?"));
    const suggested = await screen.findByRole("region", { name: "Suggested questions" });
    expect(within(suggested).getAllByRole("button").length).toBeGreaterThan(0);
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
