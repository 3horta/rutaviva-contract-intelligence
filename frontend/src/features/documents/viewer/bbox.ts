import type { BBox } from "../../../api/types";

export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Extra room around the text so the outline does not touch the glyphs. */
export const HIGHLIGHT_PADDING = 3;

/**
 * Converts a bbox in PDF points (origin top-left, as the API returns it) to
 * CSS pixels over a page rendered `renderedWidth` pixels wide.
 *
 * The scale comes from the page width the chunk was measured on, so the box
 * lands in the right place whatever the size the page is drawn at.
 */
export function scaleBBox(
  bbox: BBox,
  pageWidth: number,
  renderedWidth: number,
  padding = HIGHLIGHT_PADDING,
): Rect | null {
  if (pageWidth <= 0 || renderedWidth <= 0) return null;
  const [x0, top, x1, bottom] = bbox;
  if (x1 < x0 || bottom < top) return null;

  const scale = renderedWidth / pageWidth;
  return {
    left: x0 * scale - padding,
    top: top * scale - padding,
    width: (x1 - x0) * scale + padding * 2,
    height: (bottom - top) * scale + padding * 2,
  };
}
