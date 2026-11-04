import { fireEvent, screen, within } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { setMockTimeScale } from "../mocks/chat";
import { mockDocumentId } from "../mocks/data";
import { renderApp } from "./renderApp";

/**
 * axe over each page once its data is on screen. Contrast is off here: jsdom
 * has no layout, so it is checked on the design tokens (styles/contrast.test.ts).
 */
async function seriousViolations(container: HTMLElement) {
  const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
  return results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

describe("accessibility", () => {
  it("dashboard", async () => {
    const { container } = renderApp({ route: "/dashboard" });
    await screen.findByText("Latest uploads");
    await screen.findAllByText("fuel_supply_agreement_petroruta.pdf");
    expect(await seriousViolations(container)).toEqual([]);
  });

  it("documents", async () => {
    const { container } = renderApp({ route: "/documents" });
    await screen.findByText("fuel_supply_agreement_petroruta.pdf");
    expect(await seriousViolations(container)).toEqual([]);
  });

  it("document detail", async () => {
    const { container } = renderApp({ route: `/documents/${mockDocumentId(1, 1)}` });
    await screen.findByRole("heading", { name: "fuel_supply_agreement_petroruta.pdf" });
    expect(await seriousViolations(container)).toEqual([]);
  });

  it("chat with an answered conversation", async () => {
    const { container } = renderApp({ route: "/chat" });
    const history = await screen.findByRole("navigation", { name: "Conversation history" });
    fireEvent.click(await within(history).findByText("How does the tire supply agreement renew?"));
    await screen.findByRole("region", { name: "Sources" });
    await screen.findByRole("region", { name: "Suggested questions" });
    expect(await seriousViolations(container)).toEqual([]);
  });

  it("chat with an interrupted turn", async () => {
    const { container } = renderApp({ route: "/chat" });
    setMockTimeScale(1); // real phase durations, so there is time to stop
    const box = await screen.findByLabelText(/^Question/);
    fireEvent.change(box, { target: { value: "What is the payment term in the fuel supply agreement?" } });
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.click(await screen.findByRole("button", { name: "Stop the answer" }));
    await screen.findByRole("article", { name: "Interrupted answer" }, { timeout: 4_000 });
    setMockTimeScale(0);
    expect(await seriousViolations(container)).toEqual([]);
  }, 10_000);
});
