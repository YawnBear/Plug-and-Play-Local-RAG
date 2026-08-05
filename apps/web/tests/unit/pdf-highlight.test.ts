import { describe, expect, it } from "vitest";

import {
  ocrRegionRectangles,
  resolveTextQuote,
  textSpanRectangles,
  type TextItemLike,
} from "@/features/chats/pdf-highlight";

const item = (
  str: string,
  x: number,
): TextItemLike => ({
  str,
  width: str.length * 5,
  height: 10,
  transform: [1, 0, 0, 10, x, 100],
});

describe("PDF citation highlight resolution", () => {
  it("resolves a uniquely contextualized exact quote across text items", () => {
    const items = [item("before repeated", 0), item("quote after", 80)];

    expect(
      resolveTextQuote(items, {
        exact: "repeated quote",
        prefix: "before ",
        suffix: " after",
        sha256: "a".repeat(64),
      }),
    ).toEqual([
      { itemIndex: 0, start: 7, end: 15 },
      { itemIndex: 1, start: 0, end: 5 },
    ]);
  });

  it("refuses ambiguous exact matches", () => {
    const items = [item("same same", 0)];

    expect(
      resolveTextQuote(items, {
        exact: "same",
        prefix: "",
        suffix: "",
        sha256: "a".repeat(64),
      }),
    ).toBeNull();
  });

  it("maps normalized grapheme clusters back to their complete source span", () => {
    const items = [item("e\u0301", 0)];

    expect(
      resolveTextQuote(items, {
        exact: "é",
        prefix: "",
        suffix: "",
        sha256: "a".repeat(64),
      }),
    ).toEqual([{ itemIndex: 0, start: 0, end: 2 }]);
  });

  it("bounds text highlights along a rotated text baseline", () => {
    const rectangles = textSpanRectangles(
      [
        {
          str: "vertical",
          width: 40,
          height: 10,
          transform: [0, 1, -10, 0, 100, 100],
        },
      ],
      [{ itemIndex: 0, start: 0, end: 8 }],
      { scale: 1, transform: [1, 0, 0, 1, 0, 0] },
    );

    expect(rectangles).toEqual([{ x: 90, y: 100, width: 10, height: 40 }]);
  });

  it.each([
    [0, { x: 10, y: 80, width: 30, height: 80 }],
    [90, { x: 80, y: 10, width: 80, height: 30 }],
    [180, { x: 60, y: 40, width: 30, height: 80 }],
    [270, { x: 40, y: 60, width: 80, height: 30 }],
  ])(
    "maps OCR regions through the active %i-degree viewport",
    (rotation, expected) => {
      const convertToViewportPoint = (x: number, y: number) => {
        if (rotation === 90) return [y, x];
        if (rotation === 180) return [100 - x, 200 - y];
        if (rotation === 270) return [200 - y, 100 - x];
        return [x, y];
      };
      const result = ocrRegionRectangles(
        [{ x: 0.1, y: 0.2, width: 0.3, height: 0.4 }],
        [0, 0, 100, 200],
        { convertToViewportPoint },
      );

      expect(result).toEqual([expected]);
    },
  );
});
