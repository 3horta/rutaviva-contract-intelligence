import type { ChunkOut, DocumentOut, FailureCode } from "../api/types";
import { contractSpec, type ContractKind } from "./contracts";
import { buildPdf, layoutContract, PAGE_HEIGHT, PAGE_WIDTH } from "./pdf";

export const EMBEDDING_MODEL = "gemini-embedding-001";

/**
 * A document of the in-memory backend. `timeline` makes a document move from
 * pending to processing to its outcome as time passes, like the real worker.
 */
export interface MockDocument {
  doc: DocumentOut;
  chunks: ChunkOut[];
  file: () => Uint8Array;
  /** Size and name identify a re-upload of the same file (the API uses its hash). */
  size: number;
  timeline?: {
    processingAt: number;
    doneAt: number;
    final: Partial<DocumentOut>;
  };
}

interface Seed {
  filename: string;
  supplier: string;
  kind: ContractKind;
  daysAgo: number;
  failure?: { code: FailureCode; detail: string | null };
  /** Seconds from start-up until it finishes: shows polling in action. */
  inFlight?: { processingIn: number; doneIn: number };
  estimatedTokens?: boolean;
}

const TENANT_SEEDS: Record<string, Seed[]> = {
  tenant_a: [
    { filename: "fuel_supply_agreement_petroruta.pdf", supplier: "PetroRuta Fuels, Inc.", kind: "fuel", daysAgo: 1 },
    { filename: "tire_supply_agreement_frontera.pdf", supplier: "Frontera Tire & Retread Co.", kind: "tires", daysAgo: 2 },
    { filename: "tire_supply_exhibit_b_sla_frontera.pdf", supplier: "Frontera Tire & Retread Co.", kind: "tires", daysAgo: 2, estimatedTokens: true },
    { filename: "fleet_maintenance_agreement_rio_bravo.pdf", supplier: "Rio Bravo Fleet Services", kind: "maintenance", daysAgo: 3 },
    {
      filename: "corrupted_contract.pdf",
      supplier: "Unknown",
      kind: "carrier",
      daysAgo: 3,
      failure: { code: "corrupt_pdf", detail: "Invalid cross-reference table: startxref points outside the file." },
    },
    { filename: "warehouse_lease_laredo_dc2.pdf", supplier: "Mines Road Industrial Partners", kind: "lease", daysAgo: 5 },
    { filename: "customs_brokerage_services_aduanas_norte.pdf", supplier: "Aduanas del Norte Brokerage", kind: "customs", daysAgo: 6 },
    {
      filename: "telematics_subscription_trackline.pdf",
      supplier: "TrackLine Telematics",
      kind: "telematics",
      daysAgo: 7,
      failure: { code: "embedding_unavailable", detail: "Embedding provider returned 503 after 3 retries." },
    },
    { filename: "trailer_rental_agreement_lonestar.pdf", supplier: "Lone Star Trailer Rentals", kind: "trailers", daysAgo: 8 },
    { filename: "driver_staffing_agreement_roadready.pdf", supplier: "RoadReady Staffing", kind: "staffing", daysAgo: 9, estimatedTokens: true },
    {
      filename: "motor_carrier_agreement_sierra_madre.pdf",
      supplier: "Sierra Madre Transport",
      kind: "carrier",
      daysAgo: 10,
      failure: { code: "timeout", detail: "Processing exceeded the 300 s limit." },
    },
    { filename: "fuel_card_program_agreement_petroruta.pdf", supplier: "PetroRuta Fuels, Inc.", kind: "fuel", daysAgo: 12 },
    {
      filename: "yard_jockey_services_agreement.pdf",
      supplier: "Border Yard Services",
      kind: "staffing",
      daysAgo: 13,
      failure: { code: "worker_lost", detail: null },
    },
    { filename: "cargo_insurance_policy_centinela.pdf", supplier: "Centinela Insurance Group", kind: "insurance", daysAgo: 15 },
    { filename: "reefer_maintenance_addendum_rio_bravo.pdf", supplier: "Rio Bravo Fleet Services", kind: "maintenance", daysAgo: 16 },
    {
      filename: "pallet_supply_agreement_tejas.pdf",
      supplier: "Tejas Pallet Supply",
      kind: "tires",
      daysAgo: 18,
      failure: { code: "internal_error", detail: "Unexpected error while writing chunks." },
    },
    { filename: "cross_dock_lease_colombia_bridge.pdf", supplier: "Colombia Bridge Logistics Park", kind: "lease", daysAgo: 20 },
    { filename: "eld_hardware_purchase_trackline.pdf", supplier: "TrackLine Telematics", kind: "telematics", daysAgo: 22 },
    { filename: "customs_bond_agreement_aduanas_norte.pdf", supplier: "Aduanas del Norte Brokerage", kind: "customs", daysAgo: 25 },
    { filename: "chassis_rental_agreement_lonestar.pdf", supplier: "Lone Star Trailer Rentals", kind: "trailers", daysAgo: 27 },
    { filename: "tire_retread_program_frontera.pdf", supplier: "Frontera Tire & Retread Co.", kind: "tires", daysAgo: 29 },
    { filename: "fleet_washing_services_agreement.pdf", supplier: "Clean Rig Services", kind: "maintenance", daysAgo: 0, inFlight: { processingIn: 4, doneIn: 12 } },
    { filename: "hazmat_carrier_addendum_sierra_madre.pdf", supplier: "Sierra Madre Transport", kind: "carrier", daysAgo: 0, inFlight: { processingIn: 0, doneIn: 8 } },
  ],
  tenant_b: [
    { filename: "fleet_insurance_policy_centinela.pdf", supplier: "Centinela Insurance Group", kind: "insurance", daysAgo: 1 },
    { filename: "motor_carrier_agreement_halcon.pdf", supplier: "Transportes Halcon", kind: "carrier", daysAgo: 4, estimatedTokens: true },
    {
      filename: "scanned_lease_addendum_no_text.pdf",
      supplier: "Nuevo Laredo Industrial",
      kind: "lease",
      daysAgo: 6,
      failure: { code: "empty_pdf", detail: "No extractable text on any of the 2 pages." },
    },
  ],
};

const pad = (value: number, length: number) => String(value).padStart(length, "0");

/** Stable, UUID-shaped ids, so links to a chunk keep working across reloads. */
export const mockDocumentId = (tenant: number, index: number) =>
  `d0c${pad(tenant, 5)}-0000-4000-8000-${pad(index, 12)}`;
const mockChunkId = (documentId: string, index: number) =>
  `c${documentId.slice(1, 8)}-${pad(index, 4)}-4000-9000-${documentId.slice(-12)}`;

const titleFromFilename = (filename: string) =>
  filename
    .replace(/\.pdf$/i, "")
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");

/** Bytes that are not a PDF at all: what a corrupt upload looks like. */
const corruptBytes = () => new TextEncoder().encode("%PDF-1.4\n%garbage\x00\x01 truncated");

function buildDocument(tenant: number, index: number, seed: Seed, now: number): MockDocument {
  const id = mockDocumentId(tenant, index);
  const createdAt = now - seed.daysAgo * 86_400_000 - index * 3_600_000;
  const layout = layoutContract(contractSpec(titleFromFilename(seed.filename), seed.supplier, seed.kind));

  const chunks: ChunkOut[] = layout.blocks.map((block, chunkIndex) => ({
    id: mockChunkId(id, chunkIndex),
    document_id: id,
    page: block.page,
    chunk_index: chunkIndex,
    content: block.text,
    // Every seventh chunk has no known position, as text from some PDFs does.
    bbox: chunkIndex % 7 === 6 ? null : block.bbox,
    page_width: PAGE_WIDTH,
    page_height: PAGE_HEIGHT,
  }));

  const failure = seed.failure;
  const readable = failure?.code !== "corrupt_pdf";
  const pageCount = failure?.code === "corrupt_pdf" ? null : layout.pages.length;
  const embedded = !failure;

  const ready: Partial<DocumentOut> = {
    status: failure ? "failed" : "ready",
    failure_code: failure?.code ?? null,
    failure_detail: failure?.detail ?? null,
    page_count: failure?.code === "empty_pdf" ? 2 : pageCount,
    chunk_count: embedded ? chunks.length : failure?.code === "corrupt_pdf" ? null : 0,
    embedding_model: embedded ? EMBEDDING_MODEL : null,
    metrics: embedded
      ? {
          processing_ms: 1800 + ((index * 977) % 7000),
          embedding_calls: Math.ceil(chunks.length / 8),
          embedding_tokens: chunks.reduce((sum, c) => sum + Math.round(c.content.length / 4), 0),
          tokens_estimated: Boolean(seed.estimatedTokens),
        }
      : failure && failure.code !== "corrupt_pdf"
        ? { processing_ms: 300_000, embedding_calls: 0, embedding_tokens: 0, tokens_estimated: false }
        : null,
    updated_at: new Date(createdAt + 9_000).toISOString(),
  };

  const pending: DocumentOut = {
    id,
    filename: seed.filename,
    status: "pending",
    failure_code: null,
    failure_detail: null,
    page_count: null,
    chunk_count: null,
    embedding_model: null,
    metrics: null,
    created_at: new Date(createdAt).toISOString(),
    updated_at: new Date(createdAt).toISOString(),
  };

  const file = () => {
    if (!readable) return corruptBytes();
    if (failure?.code === "empty_pdf") return buildPdf([[], []]);
    return buildPdf(layout.pages);
  };

  const mock: MockDocument = {
    doc: seed.inFlight ? pending : { ...pending, ...ready },
    chunks: embedded ? chunks : [],
    file,
    size: 40_000 + index * 1_337,
  };
  if (seed.inFlight) {
    mock.timeline = {
      processingAt: now + seed.inFlight.processingIn * 1000,
      doneAt: now + seed.inFlight.doneIn * 1000,
      final: { ...ready, updated_at: new Date(now + seed.inFlight.doneIn * 1000).toISOString() },
    };
  }
  return mock;
}

export function seedTenants(now: number): Map<string, MockDocument[]> {
  const tenants = new Map<string, MockDocument[]>();
  Object.entries(TENANT_SEEDS).forEach(([tenantId, seeds], tenant) => {
    tenants.set(
      tenantId,
      seeds.map((seed, index) => buildDocument(tenant + 1, index + 1, seed, now)),
    );
  });
  return tenants;
}
