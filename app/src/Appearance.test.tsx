import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const windowTheme = vi.hoisted(() => vi.fn(() => Promise.resolve()));
vi.mock("./bridge", () => ({ windowTheme }));

import Appearance, { applyTheme, followTheme, savedTheme, THEME_KEY } from "./Appearance";

const root = document.documentElement;
beforeEach(() => {
  localStorage.clear();
  delete root.dataset.theme;
  windowTheme.mockClear();
});
afterEach(cleanup);

test("starts on Match the Mac, which leaves the page to the Mac's setting", () => {
  expect(savedTheme()).toBe("system");
  render(<Appearance />);
  fireEvent.click(screen.getByRole("button", { name: "Appearance" }));
  expect(screen.getByRole("button", { name: "Match the Mac" }).getAttribute("aria-pressed")).toBe("true");
  expect(root.dataset.theme).toBeUndefined();
});

test("Dark is kept, applied to the page and the window, and the menu closes", () => {
  render(<Appearance />);
  const icon = screen.getByRole("button", { name: "Appearance" });
  expect(icon.getAttribute("title")).toBe("Appearance");
  fireEvent.click(icon);
  fireEvent.click(screen.getByRole("button", { name: "Dark" }));
  expect(localStorage.getItem("assieme.theme")).toBe("dark");
  expect(root.dataset.theme).toBe("dark");
  expect(windowTheme).toHaveBeenCalledWith("dark");
  expect(screen.queryByRole("group", { name: "Appearance" })).toBeNull();
  fireEvent.click(icon);
  expect(screen.getByRole("button", { name: "Dark" }).getAttribute("aria-pressed")).toBe("true");
});

test("Match the Mac forgets the choice; Escape closes the menu", () => {
  localStorage.setItem("assieme.theme", "light");
  applyTheme(savedTheme());
  expect(root.dataset.theme).toBe("light");
  render(<Appearance />);
  fireEvent.click(screen.getByRole("button", { name: "Appearance" }));
  fireEvent.click(screen.getByRole("button", { name: "Match the Mac" }));
  expect(localStorage.getItem("assieme.theme")).toBeNull();
  expect(root.dataset.theme).toBeUndefined();
  expect(windowTheme).toHaveBeenLastCalledWith("system");
  fireEvent.click(screen.getByRole("button", { name: "Appearance" }));
  fireEvent.keyDown(screen.getByRole("group", { name: "Appearance" }), { key: "Escape" });
  expect(screen.queryByRole("group", { name: "Appearance" })).toBeNull();
});

test("a change made in the other window is followed, and junk in storage means Match the Mac", () => {
  followTheme();
  localStorage.setItem("assieme.theme", "dark");
  window.dispatchEvent(new StorageEvent("storage", { key: "assieme.theme" }));
  expect(root.dataset.theme).toBe("dark");
  localStorage.setItem("assieme.theme", "purple");
  expect(savedTheme()).toBe("system");
});

test("opening puts focus on the current choice; Escape and a pick give it back to the icon", () => {
  render(<Appearance />);
  const icon = screen.getByRole("button", { name: "Appearance" });
  fireEvent.click(icon);
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Match the Mac" }));
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(document.activeElement).toBe(icon);
  fireEvent.click(icon);
  fireEvent.click(screen.getByRole("button", { name: "Light" }));
  expect(document.activeElement).toBe(icon);
});

test("a press outside closes the menu and still reaches what it was on", () => {
  const elsewhere = vi.fn();
  render(<><button type="button" onClick={elsewhere}>Board</button><Appearance /></>);
  fireEvent.click(screen.getByRole("button", { name: "Appearance" }));
  screen.getByRole("group", { name: "Appearance" });
  expect(document.querySelector(".appearance-away")).toBeNull(); // nothing may sit over the page and eat the click
  const board = screen.getByRole("button", { name: "Board" });
  fireEvent.pointerDown(board);
  fireEvent.click(board);
  expect(screen.queryByRole("group", { name: "Appearance" })).toBeNull();
  expect(elsewhere).toHaveBeenCalledTimes(1);
});

test("index.html reads the same key and values before the first paint", async () => {
  // @ts-expect-error type error without @types/node package, as in vite.config.ts
  const { readFileSync } = (await import("node:fs")) as { readFileSync: (f: string, e: string) => string };
  const html = readFileSync("index.html", "utf8");
  expect(html).toContain(`localStorage.getItem("${THEME_KEY}")`);
  expect(html).toContain('t === "light" || t === "dark"');
});
