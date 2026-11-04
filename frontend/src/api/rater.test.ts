import { afterEach, describe, expect, it, vi } from "vitest";
import { raterId, resetRaterIdForTests } from "./rater";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  resetRaterIdForTests();
});

describe("raterId", () => {
  it("makes up a UUID once and keeps it in localStorage", () => {
    const id = raterId();
    expect(id).toMatch(UUID);
    expect(localStorage.getItem("rutaviva.raterId")).toBe(id);
    resetRaterIdForTests(); // a reload: only storage is left
    expect(raterId()).toBe(id);
  });

  it("replaces a stored value that is not a UUID", () => {
    localStorage.setItem("rutaviva.raterId", "not-a-uuid");
    expect(raterId()).toMatch(UUID);
  });

  it("keeps one id in memory when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    const id = raterId();
    expect(id).toMatch(UUID);
    expect(raterId()).toBe(id);
  });
});
