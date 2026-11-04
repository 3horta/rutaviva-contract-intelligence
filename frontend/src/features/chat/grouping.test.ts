import { describe, expect, it } from "vitest";
import type { ConversationSummary } from "../../api/types";
import { groupConversations, groupOf } from "./grouping";

const now = new Date(2026, 10, 3, 15, 0);
const daysAgo = (days: number, hour = 12) => new Date(2026, 10, 3 - days, hour, 0);

const conversation = (id: string, updated: Date): ConversationSummary => ({
  id,
  title: id,
  created_at: updated.toISOString(),
  updated_at: updated.toISOString(),
  message_count: 2,
});

describe("groupOf", () => {
  it.each([
    [daysAgo(0, 0), "Today"],
    [daysAgo(1, 23), "Yesterday"],
    [daysAgo(1, 0), "Yesterday"],
    [daysAgo(2), "Last 7 days"],
    [daysAgo(7), "Last 7 days"],
    [daysAgo(8), "Last 30 days"],
    [daysAgo(30), "Last 30 days"],
    [daysAgo(31), "Older"],
  ])("puts %s in %s", (date, label) => {
    expect(groupOf(date, now)).toBe(label);
  });
});

describe("groupConversations", () => {
  it("keeps a fixed group order and sorts newest first inside", () => {
    const groups = groupConversations(
      [
        conversation("old", daysAgo(90)),
        conversation("today-early", daysAgo(0, 8)),
        conversation("yesterday", daysAgo(1)),
        conversation("today-late", daysAgo(0, 14)),
      ],
      now,
    );
    expect(groups.map((g) => [g.label, g.items.map((c) => c.id)])).toEqual([
      ["Today", ["today-late", "today-early"]],
      ["Yesterday", ["yesterday"]],
      ["Older", ["old"]],
    ]);
  });
});
