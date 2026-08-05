import type { HighlightAnchor } from "./contracts";

export interface TextItemLike {
  str: string;
  width: number;
  height: number;
  transform: readonly number[];
  hasEOL?: boolean;
}

export interface TextItemSpan {
  itemIndex: number;
  start: number;
  end: number;
}

export interface OverlayRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface MappedCharacter {
  itemIndex: number;
  characterIndex: number;
  characterEnd: number;
}

export function normalizeHighlightText(value: string): string {
  return value.normalize("NFKC").replaceAll("\u00ad", "").replace(/\s+/g, " ").trim();
}

export function resolveTextQuote(
  items: readonly TextItemLike[],
  selector: Extract<
    HighlightAnchor["pages"][number],
    { kind: "text_quote" }
  >["selector"],
): TextItemSpan[] | null {
  const mapped = mappedNormalizedStream(items);
  const candidates: number[] = [];
  let offset = 0;
  while (offset <= mapped.text.length - selector.exact.length) {
    const found = mapped.text.indexOf(selector.exact, offset);
    if (found < 0) break;
    const prefix = mapped.text.slice(
      Math.max(0, found - selector.prefix.length),
      found,
    );
    const suffix = mapped.text.slice(
      found + selector.exact.length,
      found + selector.exact.length + selector.suffix.length,
    );
    if (prefix.endsWith(selector.prefix) && suffix.startsWith(selector.suffix)) {
      candidates.push(found);
    }
    offset = found + 1;
  }
  if (candidates.length !== 1) return null;
  const selected = mapped.map.slice(
    candidates[0],
    candidates[0] + selector.exact.length,
  );
  const spans = new Map<number, { start: number; end: number }>();
  for (const character of selected) {
    if (character.itemIndex < 0) continue;
    const existing = spans.get(character.itemIndex);
    spans.set(character.itemIndex, {
      start: Math.min(existing?.start ?? character.characterIndex, character.characterIndex),
      end: Math.max(existing?.end ?? character.characterEnd, character.characterEnd),
    });
  }
  return [...spans.entries()].map(([itemIndex, span]) => ({ itemIndex, ...span }));
}

export function textSpanRectangles(
  items: readonly TextItemLike[],
  spans: readonly TextItemSpan[],
  viewport: {
    scale: number;
    transform: readonly number[];
  },
): OverlayRect[] {
  return spans.flatMap((span) => {
    const item = items[span.itemIndex];
    if (!item || !item.str.length || item.transform.length < 6) return [];
    const transformed = multiply(viewport.transform, item.transform);
    const itemHeight = Math.max(
      Math.hypot(transformed[2], transformed[3]),
      item.height * viewport.scale,
      1,
    );
    const ratioStart = span.start / item.str.length;
    const ratioEnd = span.end / item.str.length;
    const width = Math.max(item.width * viewport.scale, 1);
    const baselineLength = Math.hypot(transformed[0], transformed[1]);
    const baselineX = baselineLength > 0 ? transformed[0] / baselineLength : 1;
    const baselineY = baselineLength > 0 ? transformed[1] / baselineLength : 0;
    const heightLength = Math.hypot(transformed[2], transformed[3]);
    const heightX = heightLength > 0 ? transformed[2] / heightLength : 0;
    const heightY = heightLength > 0 ? transformed[3] / heightLength : -1;
    const startX = transformed[4] + baselineX * width * ratioStart;
    const startY = transformed[5] + baselineY * width * ratioStart;
    const endX = transformed[4] + baselineX * width * ratioEnd;
    const endY = transformed[5] + baselineY * width * ratioEnd;
    const corners = [
      [startX, startY],
      [endX, endY],
      [startX + heightX * itemHeight, startY + heightY * itemHeight],
      [endX + heightX * itemHeight, endY + heightY * itemHeight],
    ];
    const xValues = corners.map(([x]) => x);
    const yValues = corners.map(([, y]) => y);
    const left = Math.min(...xValues);
    const top = Math.min(...yValues);
    return [
      {
        x: left,
        y: top,
        width: Math.max(Math.max(...xValues) - left, 1),
        height: Math.max(Math.max(...yValues) - top, 1),
      },
    ];
  });
}

export function ocrRegionRectangles(
  regions: readonly { x: number; y: number; width: number; height: number }[],
  pageView: readonly number[],
  viewport: {
    convertToViewportRectangle(rect: readonly number[]): readonly number[];
  },
): OverlayRect[] {
  const [left, bottom, right, top] = pageView;
  const pageWidth = right - left;
  const pageHeight = top - bottom;
  return regions.map((region) => {
    const converted = viewport.convertToViewportRectangle([
      left + region.x * pageWidth,
      bottom + (1 - region.y - region.height) * pageHeight,
      left + (region.x + region.width) * pageWidth,
      bottom + (1 - region.y) * pageHeight,
    ]);
    const [x1, y1, x2, y2] = converted;
    return {
      x: Math.min(x1, x2),
      y: Math.min(y1, y2),
      width: Math.abs(x2 - x1),
      height: Math.abs(y2 - y1),
    };
  });
}

function mappedNormalizedStream(items: readonly TextItemLike[]): {
  text: string;
  map: MappedCharacter[];
} {
  const raw: {
    value: string;
    itemIndex: number;
    characterIndex: number;
    characterEnd: number;
  }[] = [];
  items.forEach((item, itemIndex) => {
    const previous = items[itemIndex - 1];
    if (previous && needsSeparator(previous, item)) {
      raw.push({
        value: " ",
        itemIndex: -1,
        characterIndex: 0,
        characterEnd: 0,
      });
    }
    const clusters = item.str.matchAll(/\P{Mark}\p{Mark}*|\p{Mark}+/gu);
    for (const match of clusters) {
      const cluster = match[0];
      const characterIndex = match.index;
      for (const normalized of cluster.normalize("NFKC").replaceAll("\u00ad", "")) {
        raw.push({
          value: normalized,
          itemIndex,
          characterIndex,
          characterEnd: characterIndex + cluster.length,
        });
      }
    }
  });
  const output: string[] = [];
  const map: MappedCharacter[] = [];
  let pendingSpace: MappedCharacter | null = null;
  for (const entry of raw) {
    if (/\s/u.test(entry.value)) {
      if (output.length) {
        pendingSpace = {
          itemIndex: entry.itemIndex,
          characterIndex: entry.characterIndex,
          characterEnd: entry.characterEnd,
        };
      }
      continue;
    }
    if (pendingSpace) {
      output.push(" ");
      map.push(pendingSpace);
      pendingSpace = null;
    }
    output.push(entry.value);
    map.push({
      itemIndex: entry.itemIndex,
      characterIndex: entry.characterIndex,
      characterEnd: entry.characterEnd,
    });
  }
  return { text: output.join(""), map };
}

function needsSeparator(previous: TextItemLike, current: TextItemLike): boolean {
  if (
    previous.hasEOL ||
    /\s$/u.test(previous.str) ||
    /^\s/u.test(current.str)
  ) {
    return true;
  }
  const previousX = previous.transform[4] ?? 0;
  const previousY = previous.transform[5] ?? 0;
  const currentX = current.transform[4] ?? 0;
  const currentY = current.transform[5] ?? 0;
  const lineHeight = Math.max(previous.height, current.height, 1);
  const changedLine = Math.abs(currentY - previousY) > lineHeight * 0.5;
  const horizontalGap = currentX - (previousX + previous.width);
  return changedLine || horizontalGap > lineHeight * 0.15;
}

function multiply(a: readonly number[], b: readonly number[]): number[] {
  return [
    a[0] * b[0] + a[2] * b[1],
    a[1] * b[0] + a[3] * b[1],
    a[0] * b[2] + a[2] * b[3],
    a[1] * b[2] + a[3] * b[3],
    a[0] * b[4] + a[2] * b[5] + a[4],
    a[1] * b[4] + a[3] * b[5] + a[5],
  ];
}
