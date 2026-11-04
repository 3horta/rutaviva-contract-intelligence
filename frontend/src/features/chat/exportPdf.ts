import type { jsPDF } from "jspdf";
import type { ConversationMessage } from "../../api/types";
import { parseMarkdown, plainText } from "../../lib/markdown";
import { CONFIDENCE, INTERRUPTED_TEXT, warningLabel } from "./phases";

const MARGIN = 56;
const LINE_HEIGHT = 1.4;

interface TextStyle {
  size?: number;
  bold?: boolean;
  muted?: boolean;
  /** Left indent, in points. */
  indent?: number;
}

/** Writes wrapped text top to bottom, adding pages as it fills them. */
class PdfWriter {
  private y = MARGIN;
  private readonly width: number;
  private readonly height: number;

  constructor(private readonly pdf: jsPDF) {
    this.width = pdf.internal.pageSize.getWidth();
    this.height = pdf.internal.pageSize.getHeight();
  }

  text(content: string, { size = 10.5, bold = false, muted = false, indent = 0 }: TextStyle = {}, marker?: string) {
    this.pdf.setFont("helvetica", bold ? "bold" : "normal");
    this.pdf.setFontSize(size);
    this.pdf.setTextColor(muted ? 110 : 30);
    const lineHeight = size * LINE_HEIGHT;
    const lines = this.pdf.splitTextToSize(content, this.width - MARGIN * 2 - indent) as string[];
    lines.forEach((line, index) => {
      if (this.y + lineHeight > this.height - MARGIN) {
        this.pdf.addPage();
        this.y = MARGIN;
      }
      if (marker && index === 0) this.pdf.text(marker, MARGIN + indent - 14, this.y + size);
      this.pdf.text(line, MARGIN + indent, this.y + size);
      this.y += lineHeight;
    });
  }

  space(points: number) {
    this.y += points;
  }

  rule() {
    this.pdf.setDrawColor(210);
    this.pdf.line(MARGIN, this.y, this.width - MARGIN, this.y);
    this.y += 12;
  }

  save(fileName: string) {
    this.pdf.save(fileName);
  }

  pageNumbers(title: string) {
    const pages = this.pdf.getNumberOfPages();
    for (let page = 1; page <= pages; page++) {
      this.pdf.setPage(page);
      this.pdf.setFont("helvetica", "normal");
      this.pdf.setFontSize(8);
      this.pdf.setTextColor(140);
      this.pdf.text(title.slice(0, 80), MARGIN, this.height - 28);
      this.pdf.text(`${page} / ${pages}`, this.width - MARGIN, this.height - 28, { align: "right" });
    }
  }
}

function writeAnswer(writer: PdfWriter, message: ConversationMessage) {
  if (message.status === "interrupted") {
    writer.text(INTERRUPTED_TEXT, { size: 9, muted: true });
    return;
  }
  if (message.status === "insufficient_evidence") {
    writer.text("Not enough evidence", { size: 9, bold: true, muted: true });
  }
  for (const block of parseMarkdown(message.content)) {
    if (block.type === "paragraph") {
      writer.text(block.lines.map(plainText).join("\n"));
    } else {
      block.items.forEach((item, index) =>
        writer.text(plainText(item), { indent: 16 }, block.ordered ? `${index + 1}.` : "•"),
      );
    }
    writer.space(4);
  }
  if (message.confidence && message.status === "answered") {
    writer.text(CONFIDENCE[message.confidence].label, { size: 9, muted: true });
  }
  message.warnings.forEach((warning) => writer.text(`Warning: ${warningLabel(warning)}`, { size: 9, muted: true }));
  if (message.citations.length) {
    writer.space(4);
    writer.text("Sources", { size: 9, bold: true });
    message.citations.forEach((citation) => {
      writer.text(`[${citation.document_filename}, p. ${citation.page}]`, { size: 9, indent: 16 }, "•");
      writer.text(`“${citation.quote}”`, { size: 8.5, muted: true, indent: 16 });
    });
  }
}

/** "Fuel payment terms?" -> "fuel-payment-terms.pdf". */
export function pdfFileName(title: string): string {
  const slug = title
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
  return `${slug || "conversation"}.pdf`;
}

/**
 * The conversation as a PDF: questions, answers, warnings and sources. The
 * "How it was answered" block is a screen aid and stays out. jsPDF is loaded
 * only here, so it does not weigh on the chat.
 */
export async function exportConversationPdf(title: string, messages: ConversationMessage[]): Promise<void> {
  const { jsPDF } = await import("jspdf");
  const writer = new PdfWriter(new jsPDF({ unit: "pt", format: "a4" }));

  writer.text(title, { size: 16, bold: true });
  writer.text(`Exported on ${new Date().toLocaleString("en-US")}`, { size: 9, muted: true });
  writer.space(6);
  writer.rule();

  for (const message of messages) {
    if (message.role === "user") {
      writer.space(8);
      writer.text(message.content, { size: 11.5, bold: true });
      writer.space(4);
    } else {
      writeAnswer(writer, message);
      writer.space(6);
    }
  }

  writer.pageNumbers(title);
  writer.save(pdfFileName(title));
}
