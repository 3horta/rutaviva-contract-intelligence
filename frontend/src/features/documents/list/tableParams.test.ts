import { describe, expect, it } from "vitest";
import { DEFAULT_TABLE_STATE, parseTableState, serializeTableState } from "./tableParams";

describe("table state in the URL", () => {
  it("round-trips every field", () => {
    const state = {
      page: 2,
      pageSize: 25,
      ordering: "filename" as const,
      search: "tire",
      status: "failed" as const,
    };
    expect(parseTableState(serializeTableState(state))).toEqual(state);
  });

  it("keeps the URL empty for the defaults", () => {
    expect(serializeTableState(DEFAULT_TABLE_STATE).toString()).toBe("");
  });

  it("uses a 1-based page in the URL", () => {
    expect(serializeTableState({ ...DEFAULT_TABLE_STATE, page: 1 }).get("page")).toBe("2");
  });

  it("falls back to defaults for invalid values", () => {
    const params = new URLSearchParams("page=-3&size=7&sort=drop_table&status=deleted&q=%20%20");
    expect(parseTableState(params)).toEqual(DEFAULT_TABLE_STATE);
  });
});
