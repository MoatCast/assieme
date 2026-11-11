import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const windowTheme = vi.hoisted(() => vi.fn(() => Promise.resolve()));
vi.mock("./bridge", () => ({ windowTheme }));

import Appearance, { applyTheme, followTheme, savedTheme } from "./Appearance";

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
  expect(screen.getByRole("menuitemradio", { name: "Match the Mac" }).getAttribute("aria-checked")).toBe("true");
  expect(root.dataset.theme).toBeUndefined();
});

test("Dark is kept, applied to the page and the window, and the menu closes", () => {
  render(<Appearance />);
  const icon = screen.getByRole("button", { name: "Appearance" });
  expect(icon.getAttribute("title")).toBe("Appearance");
  fireEvent.click(icon);
  fireEvent.click(screen.getByRole("menuitemradio", { name: "Dark" }));
  expect(localStorage.getItem("assieme.theme")).toBe("dark");
  expect(root.dataset.theme).toBe("dark");
  expect(windowTheme).toHaveBeenCalledWith("dark");
  expect(screen.queryByRole("menu")).toBeNull();
  fireEvent.click(icon);
  expect(screen.getByRole("menuitemradio", { name: "Dark" }).getAttribute("aria-checked")).toBe("true");
});

test("Match the Mac forgets the choice; Escape closes the menu", () => {
  localStorage.setItem("assieme.theme", "light");
  applyTheme(savedTheme());
  expect(root.dataset.theme).toBe("light");
  render(<Appearance />);
  fireEvent.click(screen.getByRole("button", { name: "Appearance" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: "Match the Mac" }));
  expect(localStorage.getItem("assieme.theme")).toBeNull();
  expect(root.dataset.theme).toBeUndefined();
  expect(windowTheme).toHaveBeenLastCalledWith("system");
  fireEvent.click(screen.getByRole("button", { name: "Appearance" }));
  fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
  expect(screen.queryByRole("menu")).toBeNull();
});

test("a change made in the other window is followed, and junk in storage means Match the Mac", () => {
  followTheme();
  localStorage.setItem("assieme.theme", "dark");
  window.dispatchEvent(new StorageEvent("storage", { key: "assieme.theme" }));
  expect(root.dataset.theme).toBe("dark");
  localStorage.setItem("assieme.theme", "purple");
  expect(savedTheme()).toBe("system");
});
