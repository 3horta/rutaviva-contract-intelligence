import { ApiError } from "../../api/errors";
import type { UploadResponse } from "../../api/types";
import {
  describeSummary,
  MAX_UPLOAD_BYTES,
  precheck,
  runUploads,
  summarize,
  type UploadItem,
} from "./uploadQueue";

const pdf = (name: string, size = 1_000, lastModified = 1) =>
  new File([new Uint8Array(size)], name, { type: "application/pdf", lastModified });

/** A promise resolved or rejected from the test, to control timing exactly. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Applies the queue's patches to a plain array, like the React provider does. */
function track(items: UploadItem[]) {
  const state = items.map((item) => ({ ...item }));
  const onChange = (index: number, patch: Partial<UploadItem>) => {
    Object.assign(state[index] as UploadItem, patch);
  };
  return { state, onChange };
}

describe("precheck", () => {
  it("queues valid PDFs", () => {
    const items = precheck([pdf("a.pdf"), pdf("b.PDF")]);
    expect(items.map((i) => i.state)).toEqual(["queued", "queued"]);
  });

  it("rejects files that are not PDFs or are too big", () => {
    const text = new File(["hello"], "notes.txt", { type: "text/plain" });
    const huge = pdf("huge.pdf", MAX_UPLOAD_BYTES + 1);
    const [first, second] = precheck([text, huge]);
    expect(first).toMatchObject({ state: "error", detail: "Not a PDF file" });
    expect(second).toMatchObject({ state: "error", detail: "Larger than 20 MB" });
  });

  it("accepts a file of exactly 20 MB", () => {
    expect(precheck([pdf("limit.pdf", MAX_UPLOAD_BYTES)])[0]?.state).toBe("queued");
  });

  it("flags the same file twice in a batch, keeping the first", () => {
    const file = pdf("contract.pdf", 500, 42);
    const sameNameOtherFile = pdf("contract.pdf", 501, 42);
    const items = precheck([file, file, sameNameOtherFile]);
    expect(items.map((i) => i.state)).toEqual(["queued", "duplicate", "queued"]);
    expect(items[1]?.detail).toBe("Repeated in this batch");
  });
});

describe("runUploads", () => {
  it("never has more than 3 uploads in flight", async () => {
    const files = Array.from({ length: 8 }, (_, i) => pdf(`f${i}.pdf`, 100 + i));
    const items = precheck(files);
    const { onChange } = track(items);
    const pending: ReturnType<typeof deferred<UploadResponse>>[] = [];
    let inFlight = 0;
    let maxInFlight = 0;

    const run = runUploads(items, files, {
      signal: new AbortController().signal,
      onChange,
      upload: async () => {
        inFlight++;
        maxInFlight = Math.max(maxInFlight, inFlight);
        const next = deferred<UploadResponse>();
        pending.push(next);
        try {
          return await next.promise;
        } finally {
          inFlight--;
        }
      },
    });

    // Release uploads one by one, as a slow server would.
    for (let released = 0; released < files.length; released++) {
      await flush();
      expect(inFlight).toBeLessThanOrEqual(3);
      pending[released]?.resolve({ document_id: `doc-${released}`, status: "pending", duplicate: false });
    }
    await run;

    expect(maxInFlight).toBe(3);
  });

  it("keeps going when one upload fails, and records each answer", async () => {
    const files = [pdf("ok.pdf", 1), pdf("broken.pdf", 2), pdf("again.pdf", 3), pdf("last.pdf", 4)];
    const items = precheck(files);
    const { state, onChange } = track(items);

    await runUploads(items, files, {
      signal: new AbortController().signal,
      onChange,
      upload: async (file) => {
        if (file.name === "broken.pdf") {
          throw new ApiError({ status: 415, title: "Unsupported", detail: "Only PDF files are accepted." });
        }
        if (file.name === "again.pdf") {
          return { document_id: "existing-id", status: "ready", duplicate: true };
        }
        return { document_id: `new-${file.name}`, status: "pending", duplicate: false };
      },
    });

    expect(state.map((i) => i.state)).toEqual(["sent", "error", "duplicate", "sent"]);
    expect(state[1]?.detail).toBe("Only PDF files are accepted.");
    expect(state[2]?.documentId).toBe("existing-id");
    expect(state[3]?.documentId).toBe("new-last.pdf");
    expect(describeSummary(summarize(state))).toBe("2 sent, 1 duplicate, 1 failed");
  });

  it("does not send files that failed the pre-checks", async () => {
    const files = [pdf("a.pdf", 1, 1), pdf("a.pdf", 1, 1), new File(["x"], "x.docx")];
    const items = precheck(files);
    const { onChange } = track(items);
    const upload = vi.fn(async () => ({ document_id: "id", status: "pending" as const, duplicate: false }));

    await runUploads(items, files, { signal: new AbortController().signal, onChange, upload });

    expect(upload).toHaveBeenCalledTimes(1);
  });

  it("starts nothing new after the batch is aborted", async () => {
    const files = Array.from({ length: 5 }, (_, i) => pdf(`f${i}.pdf`, i + 1));
    const items = precheck(files);
    const { onChange } = track(items);
    const controller = new AbortController();
    const started: ReturnType<typeof deferred<UploadResponse>>[] = [];
    const upload = vi.fn(() => {
      const next = deferred<UploadResponse>();
      started.push(next);
      return next.promise;
    });

    const run = runUploads(items, files, { signal: controller.signal, onChange, upload });
    await flush();
    expect(upload).toHaveBeenCalledTimes(3);

    // Switching tenant aborts the batch: in-flight requests reject, the rest never start.
    controller.abort();
    started.forEach((d) => d.reject(new DOMException("Aborted", "AbortError")));
    await run;

    expect(upload).toHaveBeenCalledTimes(3);
  });
});
