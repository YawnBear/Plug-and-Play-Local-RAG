import { describe, expect, it } from "vitest";

import {
  citationRoute,
  homeRoute,
  knowledgeBaseRoute,
  parseHomeRouteState,
  parseKnowledgeBaseRouteState,
} from "@/lib/route-state";

const CHAT = "11111111-1111-4111-8111-111111111111";
const FOLDER = "22222222-2222-4222-8222-222222222222";
const DOCUMENT = "33333333-3333-4333-8333-333333333333";

describe("home route state", () => {
  it("distinguishes missing, valid, and invalid chat selectors", () => {
    expect(parseHomeRouteState("")).toEqual({
      chatId: null,
      invalidChat: false,
    });
    expect(parseHomeRouteState(`?chat=${CHAT.toUpperCase()}`)).toEqual({
      chatId: CHAT,
      invalidChat: false,
    });
    expect(parseHomeRouteState("?chat=bad")).toEqual({
      chatId: null,
      invalidChat: true,
    });
  });

  it("builds only canonical home URLs", () => {
    expect(homeRoute()).toBe("/");
    expect(homeRoute(CHAT)).toBe(`/?chat=${CHAT}`);
    expect(() => homeRoute("bad")).toThrow();
  });
});

describe("knowledge-base route state", () => {
  it("validates folder and document independently and defaults a selected document to page 1", () => {
    expect(
      parseKnowledgeBaseRouteState(
        `folder=bad&document=${DOCUMENT.toUpperCase()}&page=0`,
      ),
    ).toEqual({
      folderId: null,
      documentId: DOCUMENT,
      page: 1,
      invalidFolder: true,
      invalidDocument: false,
      invalidPage: true,
    });
  });

  it("omits page state when no valid document is selected", () => {
    expect(
      parseKnowledgeBaseRouteState(`folder=${FOLDER}&document=bad&page=9`),
    ).toEqual({
      folderId: FOLDER,
      documentId: null,
      page: null,
      invalidFolder: false,
      invalidDocument: true,
      invalidPage: false,
    });
  });

  it("emits the frozen canonical query order and omission rules", () => {
    expect(knowledgeBaseRoute()).toBe("/knowledge-base");
    expect(knowledgeBaseRoute({ folderId: FOLDER })).toBe(
      `/knowledge-base?folder=${FOLDER}`,
    );
    expect(
      knowledgeBaseRoute({
        folderId: FOLDER,
        documentId: DOCUMENT,
        page: 5,
      }),
    ).toBe(
      `/knowledge-base?folder=${FOLDER}&document=${DOCUMENT}&page=5`,
    );
    expect(knowledgeBaseRoute({ documentId: DOCUMENT })).toBe(
      `/knowledge-base?document=${DOCUMENT}&page=1`,
    );
  });

  it("builds citation targets only from a live document id and cited page", () => {
    expect(citationRoute({ document_id: null, page_start: 4 })).toBeNull();
    expect(citationRoute({ document_id: DOCUMENT, page_start: 4 })).toBe(
      `/knowledge-base?document=${DOCUMENT}&page=4`,
    );
  });
});
