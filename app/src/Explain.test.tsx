import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import Explain, { EXPLAIN } from "./Explain";

afterEach(cleanup);

test("opens the text for each term", () => {
  for (const term of Object.keys(EXPLAIN) as (keyof typeof EXPLAIN)[]) {
    render(<Explain term={term} />);
    const q = screen.getByRole("button");
    expect(q).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(q);
    expect(screen.getByRole("note")).toHaveTextContent(EXPLAIN[term]);
    cleanup();
  }
  render(<Explain term="copy" />);
  screen.getByRole("button", { name: "What is a copy?" });
});

test("escape closes and focus returns", () => {
  let outer = 0;
  render(<div onKeyDown={() => outer++}><Explain term="team-rules" /></div>);
  const q = screen.getByRole("button", { name: "What are team rules?" });
  fireEvent.click(q);
  fireEvent.keyDown(screen.getByRole("note"), { key: "Escape" });
  expect(screen.queryByRole("note")).toBeNull();
  expect(q).toHaveFocus();
  expect(outer).toBe(0); // the sheet around it stays open
});

test("a click outside closes it", () => {
  render(<><p>Outside</p><Explain term="permissions" /></>);
  fireEvent.click(screen.getByRole("button", { name: "What are permissions?" }));
  fireEvent.mouseDown(screen.getByText("Outside"));
  expect(screen.queryByRole("note")).toBeNull();
});

test("no link out", () => {
  render(<Explain term="sealed-off" />);
  fireEvent.click(screen.getByRole("button", { name: "What does sealed off mean?" }));
  expect(screen.queryByRole("link")).toBeNull();
});
