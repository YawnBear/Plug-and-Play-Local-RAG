"use client";

import type { MouseEvent } from "react";
import { remark } from "remark";
import rehypeSanitize, {
  defaultSchema,
  type Options as SanitizeSchema,
} from "rehype-sanitize";
import rehypeStringify from "rehype-stringify";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";

import type { HistoricalSource } from "./contracts";

type HastNode = {
  type: string;
  value?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

const citationSchema: SanitizeSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "button"],
  attributes: {
    ...defaultSchema.attributes,
    button: [
      ...(defaultSchema.attributes?.button ?? []),
      "type",
      "className",
      "dataCitationLabel",
      "ariaLabel",
      "ariaExpanded",
      "disabled",
    ],
  },
};

function citationControls(
  sources: ReadonlyMap<string, HistoricalSource>,
  selectedLabel: string | null,
) {
  return () => (tree: HastNode) => {
    const walk = (node: HastNode, blocked: boolean): void => {
      const nextBlocked =
        blocked || node.tagName === "code" || node.tagName === "a" || node.tagName === "button";
      if (!node.children || nextBlocked) return;
      const output: HastNode[] = [];
      for (const child of node.children) {
        if (child.type !== "text" || !child.value) {
          walk(child, nextBlocked);
          output.push(child);
          continue;
        }
        let cursor = 0;
        for (const match of child.value.matchAll(/\[S[1-8]\]/g)) {
          const index = match.index ?? 0;
          const label = match[0].slice(1, -1);
          const source = sources.get(label);
          if (!source) continue;
          if (index > cursor) {
            output.push({ type: "text", value: child.value.slice(cursor, index) });
          }
          const page =
            source.page_start === source.page_end
              ? `page ${source.page_start}`
              : `pages ${source.page_start}–${source.page_end}`;
          output.push({
            type: "element",
            tagName: "button",
            properties: {
              type: "button",
              className: [
                "inline-citation",
                source.source_available
                  ? "inline-citation--available"
                  : "inline-citation--unavailable",
              ],
              dataCitationLabel: label,
              ariaLabel: source.source_available
                ? `${label}, ${source.display_name}, ${page}`
                : `${label}, ${source.display_name}, source unavailable`,
              ariaExpanded:
                source.source_available && selectedLabel === label ? "true" : "false",
              disabled: !source.source_available,
            },
            children: [{ type: "text", value: match[0] }],
          });
          cursor = index + match[0].length;
        }
        if (cursor === 0) {
          output.push(child);
        } else if (cursor < child.value.length) {
          output.push({ type: "text", value: child.value.slice(cursor) });
        }
      }
      node.children = output;
    };
    walk(tree, false);
  };
}

export function MarkdownOutput({
  children,
  className = "",
  citations = [],
  selectedLabel = null,
  onCitationSelect,
}: {
  children: string;
  className?: string;
  citations?: readonly HistoricalSource[];
  selectedLabel?: string | null;
  onCitationSelect?: (label: string) => void;
}) {
  const sourceMap = new Map(citations.map((source) => [source.label, source]));
  const processor = remark()
    .use(remarkGfm)
    .use(remarkRehype)
    .use(citationControls(sourceMap, selectedLabel))
    .use(rehypeSanitize, citationSchema)
    .use(rehypeStringify);
  const html = String(processor.processSync(children));

  const handleClick = (event: MouseEvent<HTMLDivElement>): void => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest<HTMLButtonElement>("[data-citation-label]");
    const label = button?.dataset.citationLabel;
    if (label && !button.disabled) onCitationSelect?.(label);
  };

  return (
    <div
      className={`markdown-output ${className}`.trim()}
      onClick={handleClick}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
