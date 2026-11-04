import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockFetch } from "../mocks/server";
import { renderApp } from "./renderApp";

interface Call {
  method: string;
  path: string;
  body: unknown;
}

/** The mock API, recording the feedback requests; `fail` answers them with a 500. */
function recordingFetch({ fail = false } = {}) {
  const calls: Call[] = [];
  const fetch = (input: string, init: RequestInit = {}) => {
    if (input.endsWith("/feedback")) {
      const body = typeof init.body === "string" ? JSON.parse(init.body) : null;
      calls.push({ method: (init.method ?? "GET").toUpperCase(), path: input, body });
      if (fail) {
        return Promise.resolve(
          new Response(JSON.stringify({ type: "about:blank", title: "Internal server error", status: 500 }), {
            status: 500,
            headers: { "Content-Type": "application/problem+json" },
          }),
        );
      }
    }
    return mockFetch(input, init);
  };
  return { calls, fetch };
}

async function ask(question: string) {
  const box = await screen.findByLabelText(/^Question/);
  fireEvent.change(box, { target: { value: question } });
  fireEvent.keyDown(box, { key: "Enter" });
}

async function answered(question = "What is the payment term in the fuel supply agreement?") {
  await ask(question);
  await screen.findByRole("region", { name: "Suggested questions" });
  const answer = screen.getAllByRole("article", { name: "Answer" }).at(-1);
  if (!answer) throw new Error("no answer");
  return answer;
}

const thumb = (answer: HTMLElement, name: "Useful" | "Not useful") => within(answer).getByRole("button", { name });

describe("answer feedback", () => {
  it("rates an answer, then sends an optional comment", async () => {
    const { calls, fetch } = recordingFetch();
    renderApp({ route: "/chat", fetch });
    const answer = await answered();

    expect(thumb(answer, "Useful")).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(thumb(answer, "Useful"));
    expect(thumb(answer, "Useful")).toHaveAttribute("aria-pressed", "true");
    expect(thumb(answer, "Not useful")).toHaveAttribute("aria-pressed", "false");

    // The rating is stored at once; the comment box opens, focused.
    const box = within(answer).getByLabelText(/What was useful\?/);
    expect(box).toHaveFocus();
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ method: "PUT", body: { rating: "up" } });
    expect(calls[0]?.path).toMatch(/\/conversations\/[0-9a-f-]+\/messages\/[0-9a-f-]+\/feedback$/);

    fireEvent.change(box, { target: { value: "  Cites the exact clause.  " } });
    fireEvent.click(within(answer).getByRole("button", { name: "Send" }));

    expect(await within(answer).findByText("Thanks for the feedback")).toBeInTheDocument();
    expect(within(answer).queryByRole("form", { name: "Feedback comment" })).not.toBeInTheDocument();
    expect(calls[1]).toMatchObject({ method: "PUT", body: { rating: "up", comment: "Cites the exact clause." } });
    // Focus goes back to the chosen rating.
    expect(thumb(answer, "Useful")).toHaveFocus();
  });

  it("changes the rating, cancels the comment and takes the rating back", async () => {
    const { calls, fetch } = recordingFetch();
    renderApp({ route: "/chat", fetch });
    const answer = await answered();

    fireEvent.click(thumb(answer, "Useful"));
    fireEvent.click(thumb(answer, "Not useful"));
    expect(thumb(answer, "Not useful")).toHaveAttribute("aria-pressed", "true");
    expect(thumb(answer, "Useful")).toHaveAttribute("aria-pressed", "false");
    expect(within(answer).getByLabelText(/What went wrong\?/)).toBeInTheDocument();

    // Cancel keeps the rating and closes the box.
    fireEvent.click(within(answer).getByRole("button", { name: "Cancel" }));
    expect(within(answer).queryByRole("form", { name: "Feedback comment" })).not.toBeInTheDocument();
    expect(thumb(answer, "Not useful")).toHaveAttribute("aria-pressed", "true");
    expect(within(answer).getByText("Thanks for the feedback")).toBeInTheDocument();

    // The chosen one again: no rating.
    fireEvent.click(thumb(answer, "Not useful"));
    expect(thumb(answer, "Not useful")).toHaveAttribute("aria-pressed", "false");
    expect(within(answer).queryByText("Thanks for the feedback")).not.toBeInTheDocument();
    await waitFor(() => expect(calls.map((c) => c.method)).toEqual(["PUT", "PUT", "DELETE"]));
    expect(calls[1]?.body).toEqual({ rating: "down" });
  });

  it("closes the comment box with Escape", async () => {
    renderApp({ route: "/chat" });
    const answer = await answered();
    fireEvent.click(thumb(answer, "Not useful"));
    fireEvent.keyDown(within(answer).getByLabelText(/What went wrong\?/), { key: "Escape" });
    expect(within(answer).queryByRole("form", { name: "Feedback comment" })).not.toBeInTheDocument();
    expect(thumb(answer, "Not useful")).toHaveFocus();
  });

  it("puts the previous rating back when it cannot be saved", async () => {
    const { fetch } = recordingFetch({ fail: true });
    renderApp({ route: "/chat", fetch });
    const answer = await answered();

    fireEvent.click(thumb(answer, "Useful"));
    expect(await within(answer).findByRole("alert")).toHaveTextContent("The feedback could not be saved");
    expect(thumb(answer, "Useful")).toHaveAttribute("aria-pressed", "false");
  });

  it("rates a not-enough-evidence answer too", async () => {
    renderApp({ route: "/chat" });
    await ask("What is the weather forecast for Laredo next week?");
    await screen.findByText("Not enough evidence");
    const answer = screen.getAllByRole("article", { name: "Answer" }).at(-1) as HTMLElement;
    fireEvent.click(thumb(answer, "Not useful"));
    expect(thumb(answer, "Not useful")).toHaveAttribute("aria-pressed", "true");
  });

  it("offers no rating while an answer is being written", async () => {
    renderApp({ route: "/chat" });
    await ask("What is the payment term in the fuel supply agreement?");
    expect(screen.queryByRole("button", { name: "Useful" })).not.toBeInTheDocument();
    await screen.findByRole("region", { name: "Suggested questions" });
    expect(screen.getByRole("button", { name: "Useful" })).toBeInTheDocument();
  });
});
