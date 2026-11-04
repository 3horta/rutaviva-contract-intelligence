/// <reference types="node" />
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// Read from disk (vitest runs from frontend/): the test config turns CSS imports
// off (`css: false`), `?raw` included.
const tokensCss = readFileSync(`${process.cwd()}/src/styles/tokens.css`, "utf8");

/**
 * WCAG AA (4.5:1) for the text colours of the design tokens, in both themes,
 * on every background they are drawn on. jsdom has no layout, so axe cannot
 * check contrast in the page tests; this checks it at the source.
 */

type Rgba = [number, number, number, number];

function tokens(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/--rv-([a-z0-9-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2]]));
}

const [lightBlock = "", darkBlock = ""] = tokensCss.split(':root[data-theme="dark"]');
const THEMES = { light: tokens(lightBlock), dark: { ...tokens(lightBlock), ...tokens(darkBlock) } };

function parse(value: string): Rgba {
  const hex = /^#([0-9a-f]{6})$/i.exec(value.trim());
  if (hex?.[1]) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  }
  const rgb = /^rgb\((\d+) (\d+) (\d+)(?: \/ (\d+)%)?\)$/.exec(value.trim());
  if (!rgb) throw new Error(`Unreadable colour: ${value}`);
  return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3]), rgb[4] ? Number(rgb[4]) / 100 : 1];
}

/** A translucent colour as it looks over an opaque one. */
const over = (top: Rgba, below: Rgba): Rgba => [
  top[0] * top[3] + below[0] * (1 - top[3]),
  top[1] * top[3] + below[1] * (1 - top[3]),
  top[2] * top[3] + below[2] * (1 - top[3]),
  1,
];

function luminance([r, g, b]: Rgba): number {
  const channel = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(theme: Record<string, string>, fg: string, bg: string): number {
  const surface = parse(theme.surface ?? "");
  const background = over(parse(theme[bg] ?? ""), surface);
  const text = over(parse(theme[fg] ?? ""), background);
  const [high, low] = [luminance(text), luminance(background)].sort((a, b) => b - a);
  return ((high ?? 0) + 0.05) / ((low ?? 0) + 0.05);
}

/** [text token, background token]: the pairs the components actually use. */
const PAIRS: [string, string][] = [
  ["text", "bg"],
  ["text", "surface"],
  ["text-soft", "surface"],
  ["muted", "surface"],
  ["muted", "surface-alt"],
  ["muted", "bg"],
  ["primary", "surface"],
  ["primary", "surface-alt"],
  ["primary", "primary-soft"],
  ["on-primary", "primary"],
  ["success", "success-soft"],
  ["warning", "warning-soft"],
  // The body of a "not enough evidence" answer card (Message.tsx, AnswerGuidance.tsx).
  ["text", "warning-soft"],
  ["warning", "surface-alt"],
  ["danger", "surface"],
  ["danger-text", "danger-soft"],
  ["info", "info-soft"],
  ["purple", "purple-soft"],
  ["neutral", "neutral-soft"],
];

describe.each(Object.entries(THEMES))("%s theme", (_, theme) => {
  it.each(PAIRS)("%s on %s reaches 4.5:1", (fg, bg) => {
    expect(contrast(theme, fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
});
