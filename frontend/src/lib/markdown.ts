/**
 * The small Markdown subset answers are written in: paragraphs, "-" and "1."
 * lists, and **bold**. Parsed into plain data that components draw as React
 * elements, so the model's text can never become HTML. Anything else is kept
 * as literal text.
 */

export interface Span {
  text: string;
  bold: boolean;
  /** `inline code`: shown in a monospace font, never parsed further. */
  code?: boolean;
}

/** A line of text: one or more spans. */
export type Line = Span[];

export type MarkdownBlock =
  | { type: "paragraph"; lines: Line[] }
  | { type: "list"; ordered: boolean; items: Line[] };

const BULLET_ITEM = /^\s*[-*]\s+(.+)$/;
const NUMBERED_ITEM = /^\s*\d+[.)]\s+(.+)$/;

/**
 * Splits on a delimiter that comes in pairs: odd parts are inside a pair.
 * With an unpaired delimiter the whole text is returned as outside.
 */
function splitPairs(text: string, delimiter: string): { text: string; inside: boolean }[] {
  const parts = text.split(delimiter);
  if (parts.length % 2 === 0) return [{ text, inside: false }];
  return parts.map((part, index) => ({ text: part, inside: index % 2 === 1 }));
}

/** "a **b** `c`" becomes spans; a "**" or "`" without its pair stays as text. */
export function parseInline(text: string): Line {
  const spans: Line = [];
  for (const piece of splitPairs(text, "`")) {
    if (piece.inside) {
      if (piece.text) spans.push({ text: piece.text, bold: false, code: true });
      continue;
    }
    for (const part of splitPairs(piece.text, "**")) {
      if (part.text) spans.push({ text: part.text, bold: part.inside });
    }
  }
  return spans;
}

function listItem(line: string): { ordered: boolean; text: string } | null {
  const bullet = BULLET_ITEM.exec(line);
  if (bullet?.[1]) return { ordered: false, text: bullet[1] };
  const numbered = NUMBERED_ITEM.exec(line);
  if (numbered?.[1]) return { ordered: true, text: numbered[1] };
  return null;
}

export function parseMarkdown(source: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  let current: MarkdownBlock | null = null;

  for (const raw of source.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trim();
    if (!line) {
      current = null; // a blank line ends the block
      continue;
    }
    const item = listItem(line);
    if (item) {
      if (current?.type !== "list" || current.ordered !== item.ordered) {
        current = { type: "list", ordered: item.ordered, items: [] };
        blocks.push(current);
      }
      current.items.push(parseInline(item.text.trim()));
    } else {
      if (current?.type !== "paragraph") {
        current = { type: "paragraph", lines: [] };
        blocks.push(current);
      }
      current.lines.push(parseInline(line));
    }
  }
  return blocks;
}

/** The text of a line without its formatting (copy, PDF). */
export const plainText = (line: Line): string => line.map((span) => span.text).join("");
