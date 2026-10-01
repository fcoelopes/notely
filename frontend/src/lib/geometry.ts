import type { NormalizedRect } from "../types";

export interface RectLike {
  left: number;
  top: number;
  right: number;
  bottom: number;
  width: number;
  height: number;
}

export function normalizeRects(
  selectionRects: RectLike[],
  container: RectLike,
): NormalizedRect[] {
  if (container.width <= 0 || container.height <= 0) return [];

  return selectionRects
    .filter((rect) => rect.width > 0 && rect.height > 0)
    .map((rect) => {
      const left = Math.max(rect.left, container.left);
      const top = Math.max(rect.top, container.top);
      const right = Math.min(rect.right, container.right);
      const bottom = Math.min(rect.bottom, container.bottom);

      return {
        x: (left - container.left) / container.width,
        y: (top - container.top) / container.height,
        width: Math.max(0, right - left) / container.width,
        height: Math.max(0, bottom - top) / container.height,
      };
    })
    .filter((rect) => rect.width > 0 && rect.height > 0);
}

