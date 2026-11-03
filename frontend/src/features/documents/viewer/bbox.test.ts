import { scaleBBox } from "./bbox";

describe("scaleBBox", () => {
  it("maps PDF points to CSS pixels at the rendered width", () => {
    // A Letter page (612 pt) drawn 918 px wide: scale 1.5.
    expect(scaleBBox([72, 100, 272, 130], 612, 918, 0)).toEqual({
      left: 108,
      top: 150,
      width: 300,
      height: 45,
    });
  });

  it("keeps the origin at the top-left (no vertical flip)", () => {
    const rect = scaleBBox([0, 0, 10, 10], 100, 100, 0);
    expect(rect).toEqual({ left: 0, top: 0, width: 10, height: 10 });
  });

  it("adds padding on every side", () => {
    expect(scaleBBox([10, 10, 20, 20], 100, 100, 2)).toEqual({
      left: 8,
      top: 8,
      width: 14,
      height: 14,
    });
  });

  it("scales down when the page is drawn smaller than its size", () => {
    const rect = scaleBBox([306, 396, 612, 792], 612, 306, 0);
    expect(rect).toEqual({ left: 153, top: 198, width: 153, height: 198 });
  });

  it("returns null for unusable input", () => {
    expect(scaleBBox([0, 0, 10, 10], 0, 500)).toBeNull();
    expect(scaleBBox([0, 0, 10, 10], 612, 0)).toBeNull();
    expect(scaleBBox([20, 0, 10, 10], 612, 612)).toBeNull();
  });
});
