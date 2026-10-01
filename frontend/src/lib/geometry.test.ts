import { describe, expect, it } from "vitest";
import { normalizeRects } from "./geometry";

describe("normalizeRects", () => {
  it("stores selection coordinates independently from zoom", () => {
    const rects = normalizeRects(
      [{ left: 120, top: 220, right: 320, bottom: 240, width: 200, height: 20 }],
      { left: 20, top: 20, right: 420, bottom: 820, width: 400, height: 800 },
    );

    expect(rects).toEqual([{ x: 0.25, y: 0.25, width: 0.5, height: 0.025 }]);
  });

  it("clips a selection to the page bounds", () => {
    const [rect] = normalizeRects(
      [{ left: 0, top: 10, right: 80, bottom: 50, width: 80, height: 40 }],
      { left: 20, top: 20, right: 120, bottom: 220, width: 100, height: 200 },
    );

    expect(rect).toEqual({ x: 0, y: 0, width: 0.6, height: 0.15 });
  });
});
