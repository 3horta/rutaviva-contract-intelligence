import type { ConversationSummary } from "../../api/types";

export const GROUP_LABELS = ["Today", "Yesterday", "Last 7 days", "Last 30 days", "Older"] as const;
export type GroupLabel = (typeof GROUP_LABELS)[number];

export interface ConversationGroup {
  label: GroupLabel;
  items: ConversationSummary[];
}

const DAY_MS = 86_400_000;

const startOfDay = (date: Date) => {
  const day = new Date(date);
  day.setHours(0, 0, 0, 0);
  return day;
};

/** The group of a date, by calendar days back from today (local time). */
export function groupOf(updatedAt: Date, now: Date): GroupLabel {
  // Rounded: a day across a daylight-saving change lasts 23 or 25 hours.
  const daysBack = Math.round((startOfDay(now).getTime() - startOfDay(updatedAt).getTime()) / DAY_MS);
  if (daysBack <= 0) return "Today";
  if (daysBack === 1) return "Yesterday";
  if (daysBack <= 7) return "Last 7 days";
  if (daysBack <= 30) return "Last 30 days";
  return "Older";
}

/** Groups by last activity, in a fixed order and newest first inside each group. */
export function groupConversations(conversations: ConversationSummary[], now = new Date()): ConversationGroup[] {
  const byLabel = new Map<GroupLabel, ConversationSummary[]>();
  [...conversations]
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
    .forEach((conversation) => {
      const label = groupOf(new Date(conversation.updated_at), now);
      byLabel.set(label, [...(byLabel.get(label) ?? []), conversation]);
    });
  return GROUP_LABELS.flatMap((label) => {
    const items = byLabel.get(label);
    return items ? [{ label, items }] : [];
  });
}
