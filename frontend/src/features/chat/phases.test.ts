import { describe, expect, it } from "vitest";
import { warningLabel } from "./phases";

describe("warningLabel", () => {
  it("shows the text of a known code", () => {
    expect(warningLabel("sources_disagree")).toMatch(/different values/);
  });

  it("shows an unknown code as it comes", () => {
    expect(warningLabel("new_backend_code")).toBe("new_backend_code");
  });
});
