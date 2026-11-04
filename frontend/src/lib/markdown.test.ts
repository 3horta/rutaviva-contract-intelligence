import { describe, expect, it } from "vitest";
import { parseInline, parseMarkdown, plainText } from "./markdown";

describe("parseInline", () => {
  it("splits bold spans", () => {
    expect(parseInline("Pay in **45 days** or less")).toEqual([
      { text: "Pay in ", bold: false },
      { text: "45 days", bold: true },
      { text: " or less", bold: false },
    ]);
  });

  it("keeps an unpaired ** as text", () => {
    expect(parseInline("2 ** 3")).toEqual([{ text: "2 ** 3", bold: false }]);
  });

  it("reads inline code literally, even with ** inside", () => {
    expect(parseInline("Field `net_**45**` is set")).toEqual([
      { text: "Field ", bold: false },
      { text: "net_**45**", bold: false, code: true },
      { text: " is set", bold: false },
    ]);
  });

  it("keeps an unpaired backtick as text", () => {
    expect(parseInline("it`s **ok**")).toEqual([
      { text: "it`s ", bold: false },
      { text: "ok", bold: true },
    ]);
  });

  it("never interprets HTML", () => {
    expect(parseInline("<img src=x onerror=alert(1)>")).toEqual([
      { text: "<img src=x onerror=alert(1)>", bold: false },
    ]);
  });
});

describe("parseMarkdown", () => {
  it("groups lines into paragraphs separated by blank lines", () => {
    const blocks = parseMarkdown("First line\nsecond line\n\nNext paragraph");
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toMatchObject({ type: "paragraph" });
    expect(blocks[0]?.type === "paragraph" && blocks[0].lines.map(plainText)).toEqual([
      "First line",
      "second line",
    ]);
  });

  it("reads bullet and numbered lists", () => {
    const blocks = parseMarkdown("Terms:\n- **Net 45**\n- 1% interest\n\n1. Notice\n2) Cure period");
    expect(blocks.map((b) => b.type)).toEqual(["paragraph", "list", "list"]);
    const [, bullets, numbered] = blocks;
    expect(bullets).toMatchObject({ ordered: false });
    expect(bullets?.type === "list" && bullets.items[0]).toEqual([{ text: "Net 45", bold: true }]);
    expect(numbered?.type === "list" && numbered.items.map(plainText)).toEqual(["Notice", "Cure period"]);
  });

  it("starts a new list when the kind changes", () => {
    const blocks = parseMarkdown("- a\n1. b");
    expect(blocks).toMatchObject([
      { type: "list", ordered: false },
      { type: "list", ordered: true },
    ]);
  });

  it("returns nothing for blank text", () => {
    expect(parseMarkdown("  \n\r\n")).toEqual([]);
  });
});
