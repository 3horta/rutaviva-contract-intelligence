/**
 * Builds small, valid PDFs in memory so mock mode can show real pages in the
 * viewer, with text at known positions (the bboxes of the mock chunks).
 * ASCII text only, standard Helvetica fonts, US Letter pages.
 */
import type { BBox } from "../api/types";

export const PAGE_WIDTH = 612;
export const PAGE_HEIGHT = 792;

const MARGIN_X = 72;
const MARGIN_TOP = 72;
const MARGIN_BOTTOM = 72;
const BODY_SIZE = 10.5;
const HEADING_SIZE = 12;
const TITLE_SIZE = 16;
const LEADING = 1.45;
/** Rough average glyph width of Helvetica, in ems: good enough for a highlight. */
const AVG_GLYPH_EM = 0.5;
const WRAP_CHARS = 92;

interface PdfLine {
  text: string;
  x: number;
  /** Top of the line box, from the top of the page. */
  top: number;
  size: number;
  bold: boolean;
}

export interface ContractSection {
  heading: string;
  paragraphs: string[];
}

export interface ContractSpec {
  title: string;
  subtitle: string;
  sections: ContractSection[];
}

/** A paragraph as laid out: what a chunk of the mock backend points to. */
export interface LaidOutBlock {
  page: number;
  text: string;
  bbox: BBox;
}

export interface LaidOutContract {
  pages: PdfLine[][];
  blocks: LaidOutBlock[];
}

function wrap(text: string, width = WRAP_CHARS): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(/\s+/)) {
    if (line && line.length + word.length + 1 > width) {
      lines.push(line);
      line = word;
    } else {
      line = line ? `${line} ${word}` : word;
    }
  }
  if (line) lines.push(line);
  return lines;
}

const textWidth = (text: string, size: number) => text.length * size * AVG_GLYPH_EM;

/** Flows the contract over pages, recording where each paragraph ends up. */
export function layoutContract(spec: ContractSpec): LaidOutContract {
  const pages: PdfLine[][] = [[]];
  const blocks: LaidOutBlock[] = [];
  let top = MARGIN_TOP;

  const current = () => pages[pages.length - 1] as PdfLine[];
  const ensureRoom = (height: number) => {
    if (top + height > PAGE_HEIGHT - MARGIN_BOTTOM) {
      pages.push([]);
      top = MARGIN_TOP;
    }
  };
  const add = (text: string, size: number, bold: boolean) => {
    const height = size * LEADING;
    ensureRoom(height);
    const line = { text, x: MARGIN_X, top, size, bold };
    current().push(line);
    top += height;
    return line;
  };

  add(spec.title, TITLE_SIZE, true);
  add(spec.subtitle, BODY_SIZE, false);
  top += 12;

  for (const section of spec.sections) {
    ensureRoom(HEADING_SIZE * LEADING * 3);
    top += 6;
    add(section.heading, HEADING_SIZE, true);
    for (const paragraph of section.paragraphs) {
      const wrapped = wrap(paragraph);
      // Keep a paragraph on one page, so its bbox is a single rectangle.
      ensureRoom(wrapped.length * BODY_SIZE * LEADING);
      const lines = wrapped.map((text) => add(text, BODY_SIZE, false));
      const first = lines[0] as PdfLine;
      const last = lines[lines.length - 1] as PdfLine;
      const right = Math.max(...lines.map((l) => l.x + textWidth(l.text, l.size)));
      blocks.push({
        page: pages.length,
        text: paragraph,
        bbox: [first.x, first.top, Math.min(right, PAGE_WIDTH - MARGIN_X), last.top + last.size * 1.15],
      });
      top += 5;
    }
  }
  return { pages, blocks };
}

const escapePdfText = (text: string) => text.replace(/[\\()]/g, (char) => `\\${char}`);

function contentStream(lines: PdfLine[]): string {
  return lines
    .map((line) => {
      // PDF space has its origin at the bottom-left; the baseline sits ~80% down the line box.
      const baseline = PAGE_HEIGHT - (line.top + line.size * 0.8);
      const font = line.bold ? "F2" : "F1";
      return `BT /${font} ${line.size} Tf ${line.x} ${baseline.toFixed(2)} Td (${escapePdfText(line.text)}) Tj ET`;
    })
    .join("\n");
}

/** Serialises pages of text lines into the bytes of a PDF file. */
export function buildPdf(pages: PdfLine[][]): Uint8Array {
  const objects: string[] = [];
  const pageIds: number[] = [];
  // 1 catalog, 2 page tree, 3-4 fonts, then a page and its content per page.
  pages.forEach((lines, index) => {
    const pageId = 5 + index * 2;
    const contentId = pageId + 1;
    const stream = contentStream(lines);
    pageIds.push(pageId);
    objects[pageId] =
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_WIDTH} ${PAGE_HEIGHT}] ` +
      `/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents ${contentId} 0 R >>`;
    objects[contentId] = `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`;
  });
  objects[1] = "<< /Type /Catalog /Pages 2 0 R >>";
  objects[2] = `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}] /Count ${pageIds.length} >>`;
  objects[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>";
  objects[4] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>";

  let out = "%PDF-1.4\n";
  const offsets: number[] = [];
  for (let id = 1; id < objects.length; id++) {
    offsets[id] = out.length;
    out += `${id} 0 obj\n${objects[id]}\nendobj\n`;
  }
  const xrefOffset = out.length;
  out += `xref\n0 ${objects.length}\n0000000000 65535 f \n`;
  for (let id = 1; id < objects.length; id++) {
    out += `${String(offsets[id]).padStart(10, "0")} 00000 n \n`;
  }
  out += `trailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
  return new TextEncoder().encode(out);
}
