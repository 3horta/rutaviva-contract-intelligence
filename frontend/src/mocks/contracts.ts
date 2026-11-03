import type { ContractSpec } from "./pdf";

/** Kinds of supplier contract the fictional company signs. */
export type ContractKind =
  | "fuel"
  | "tires"
  | "maintenance"
  | "insurance"
  | "carrier"
  | "lease"
  | "customs"
  | "telematics"
  | "trailers"
  | "staffing";

const SUBJECT: Record<ContractKind, string> = {
  fuel: "the supply of diesel fuel and DEF to the Buyer's fleet and yard tanks",
  tires: "the supply, mounting and retreading of commercial truck tires",
  maintenance: "preventive and corrective maintenance of tractors and trailers",
  insurance: "commercial auto liability and cargo insurance for the Buyer's fleet",
  carrier: "the transportation of freight as a contract motor carrier",
  lease: "the lease of warehouse and cross-dock space",
  customs: "customs brokerage services for cross-border shipments",
  telematics: "telematics devices, ELD software and related support",
  trailers: "the short-term rental of dry van and reefer trailers",
  staffing: "the provision of qualified CDL drivers on a temporary basis",
};

const PRICE: Record<ContractKind, string> = {
  fuel: "OPIS Laredo rack average plus a fixed margin of USD 0.085 per gallon",
  tires: "the unit prices listed in Exhibit A, fixed for the first twelve (12) months",
  maintenance: "a labor rate of USD 118 per hour plus parts at list price less 12%",
  insurance: "an annual premium of USD 412,000, payable in four quarterly installments",
  carrier: "the lane rates in Schedule 1 plus a weekly fuel surcharge",
  lease: "a base rent of USD 0.62 per square foot per month, escalating 3% annually",
  customs: "a fee of USD 65 per entry plus USD 4 per additional commercial invoice line",
  telematics: "USD 29 per unit per month, with hardware included at no charge",
  trailers: "USD 45 per day for dry vans and USD 95 per day for reefers",
  staffing: "an hourly bill rate of USD 38.50, or USD 57.75 for overtime hours",
};

/**
 * A plausible supplier contract, long enough to span a few pages. Deterministic:
 * the same inputs always produce the same text, so chunk ids stay stable.
 */
export function contractSpec(title: string, supplier: string, kind: ContractKind): ContractSpec {
  const buyer = "Rutaviva Logistics, LLC, a Texas limited liability company with offices in Laredo, Texas (the \"Buyer\")";
  return {
    title,
    subtitle: `Between ${supplier} (the "Supplier") and Rutaviva Logistics, LLC (the "Buyer")`,
    sections: [
      {
        heading: "1. Parties and Purpose",
        paragraphs: [
          `This Agreement is entered into by ${supplier} (the "Supplier") and ${buyer}. It sets out the terms under which the Supplier provides ${SUBJECT[kind]}.`,
          "The recitals and exhibits attached to this Agreement form an integral part of it. In case of conflict, the body of this Agreement prevails over the exhibits.",
        ],
      },
      {
        heading: "2. Term and Renewal",
        paragraphs: [
          "This Agreement starts on the Effective Date and remains in force for an initial term of thirty-six (36) months.",
          "It renews automatically for successive periods of twelve (12) months unless either party gives written notice of non-renewal at least ninety (90) days before the end of the then-current term.",
        ],
      },
      {
        heading: "3. Pricing and Payment",
        paragraphs: [
          `The Buyer pays the Supplier according to ${PRICE[kind]}. Prices exclude applicable sales and use taxes.`,
          "Invoices are payable within forty-five (45) days of receipt. Undisputed amounts not paid when due accrue interest at one percent (1%) per month.",
          "The Supplier may review its prices once per contract year by giving sixty (60) days' written notice, capped at the change in the Producer Price Index for the preceding twelve months.",
        ],
      },
      {
        heading: "4. Service Levels",
        paragraphs: [
          "The Supplier shall meet the service levels described in the applicable exhibit. Performance is measured monthly and reported to the Buyer by the tenth business day of the following month.",
          "If the Supplier misses a service level in two consecutive months, the Buyer is entitled to a service credit of five percent (5%) of the monthly charges for each additional month the failure persists.",
        ],
      },
      {
        heading: "5. Insurance and Liability",
        paragraphs: [
          "The Supplier shall maintain commercial general liability insurance of at least USD 2,000,000 per occurrence and shall name the Buyer as an additional insured.",
          "Neither party is liable for indirect, incidental or consequential damages. Each party's aggregate liability is limited to the fees paid or payable in the twelve (12) months preceding the claim, except for breaches of confidentiality or indemnification obligations.",
        ],
      },
      {
        heading: "6. Termination",
        paragraphs: [
          "Either party may terminate this Agreement for material breach if the breach is not cured within thirty (30) days after written notice describing it in reasonable detail.",
          "The Buyer may terminate for convenience on one hundred twenty (120) days' written notice, paying only for products and services delivered up to the termination date.",
        ],
      },
      {
        heading: "7. Governing Law",
        paragraphs: [
          "This Agreement is governed by the laws of the State of Texas. The state and federal courts located in Webb County, Texas, have exclusive jurisdiction over any dispute arising from it.",
        ],
      },
    ],
  };
}
