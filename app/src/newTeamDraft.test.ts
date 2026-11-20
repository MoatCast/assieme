import { afterEach, expect, test, vi } from "vitest";
import { clearDraft, loadDraft, saveDraft, type Saved } from "./newTeamDraft";

const S: Saved = { machine: null, project: "/Users/me/shop", task: "Fix login", roles: [], chosen: "full", practices: { reviews: false }, at: "team" };
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

test("a saved draft round-trips", () => {
  saveDraft(S);
  expect(loadDraft()).toEqual(S);
  clearDraft();
  expect(loadDraft()).toBeNull();
});

test("an unreadable value reads as none", () => {
  localStorage.setItem("assieme.newteam.draft", "{not json");
  expect(loadDraft()).toBeNull();
  localStorage.setItem("assieme.newteam.draft", JSON.stringify({ project: 3 }));
  expect(loadDraft()).toBeNull();
});

test("storage that throws is ignored", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("full"); });
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  expect(() => saveDraft(S)).not.toThrow();
  expect(loadDraft()).toBeNull();
  expect(() => clearDraft()).not.toThrow();
});
