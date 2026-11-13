import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import Musician from "./Musician";
import { MUSICIANS_KEY, savedMusicianMode, setMusicianMode } from "./musicianMode";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

test("default is animated; a bad stored value is animated", () => {
  expect(savedMusicianMode()).toBe("animated");
  localStorage.setItem(MUSICIANS_KEY, "loud");
  expect(savedMusicianMode()).toBe("animated");
  localStorage.setItem(MUSICIANS_KEY, "off");
  expect(savedMusicianMode()).toBe("off");
});

test("still draws the play pose with no motion, and the hand for needs you", () => {
  setMusicianMode("still");
  render(<Musician instrument="violin" state="working" role="b" />);
  const img = screen.getByRole("img");
  expect(img.className).not.toContain("playing");
  expect(img.querySelectorAll("g")).toHaveLength(1);
  cleanup();
  const { container } = render(<Musician instrument="violin" state="asking" role="b" />);
  expect([...container.querySelectorAll("rect")].some((r) => r.getAttribute("fill")?.includes("needs-you"))).toBe(true);
});

test("off draws the dot, keeps the words for screen readers, and no picture", () => {
  setMusicianMode("off");
  const { container } = render(<Musician instrument="violin" state="asking" role="builder-2" />);
  expect(container.querySelector("svg")).toBeNull();
  expect(container.querySelector(".dot")).toBeTruthy();
  expect(screen.getByRole("img", { name: "builder-2, violin, needs you" })).toBeTruthy();
});

test("a change in another window applies at once", () => {
  render(<Musician instrument="violin" state="working" role="b" />);
  expect(screen.getByRole("img").querySelector("svg")).toBeTruthy();
  act(() => {
    localStorage.setItem(MUSICIANS_KEY, "off");
    window.dispatchEvent(new StorageEvent("storage", { key: MUSICIANS_KEY }));
  });
  expect(screen.getByRole("img").querySelector("svg")).toBeNull();
});

test("a change in this window applies at once", () => {
  render(<Musician instrument="violin" state="working" role="b" />);
  act(() => setMusicianMode("still"));
  expect(screen.getByRole("img").className).not.toContain("playing");
});
