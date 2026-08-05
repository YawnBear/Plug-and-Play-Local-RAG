import { describe, expect, it } from "vitest";

import KnowledgeBasePage from "@/app/knowledge-base/page";
import { KnowledgeBaseWorkspace } from "@/features/library/knowledge-base-workspace";

import { DOCUMENT, FOLDER_A } from "../library-fixtures";

describe("Knowledge Base server page", () => {
  it("awaits promised params and renders only the workspace route boundary", async () => {
    const element = await KnowledgeBasePage({
      searchParams: Promise.resolve({
        folder: FOLDER_A,
        document: DOCUMENT,
        page: "4",
      }),
    });
    expect(element.type).toBe(KnowledgeBaseWorkspace);
    expect(element.props.initialRoute).toEqual({
      folderId: FOLDER_A,
      documentId: DOCUMENT,
      page: 4,
      invalidFolder: false,
      invalidDocument: false,
      invalidPage: false,
    });
  });

  it("normalizes an invalid cited page to page 1 without adding a main landmark", async () => {
    const element = await KnowledgeBasePage({
      searchParams: Promise.resolve({
        document: DOCUMENT,
        page: "0",
      }),
    });
    expect(element.type).toBe(KnowledgeBaseWorkspace);
    expect(element.props.initialRoute.page).toBe(1);
    expect(element.props.initialRoute.invalidPage).toBe(true);
  });
});
