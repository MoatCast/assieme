import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import TeamRules from "./TeamRules";
import { PRACTICES } from "./practicesFixture";

afterEach(cleanup);
const PANEL = { steps: ["project", "task", "team", "start"] as ("project" | "task" | "team" | "start")[], at: "start" as const, answers: {}, onGo: () => {} };

function rules(start: Record<string, boolean> = {}) {
  const h = { onChange: vi.fn(), onDone: vi.fn() };
  function Harness() {
    const [v, setV] = useState(start);
    return <TeamRules panel={PANEL} practices={PRACTICES} project="/Users/me/checkout-service" value={v} onDone={h.onDone}
      onChange={(x) => { h.onChange(x); setV(x); }} />;
  }
  render(<Harness />);
  return h;
}

test("groups are on and switch off, for this team only", () => {
  const h = rules();
  screen.getByRole("heading", { name: "Team rules for checkout-service" });
  screen.getByText("All on. Each one came from a problem on a real team. Turning one off changes this team only.");
  const switches = screen.getAllByRole("switch") as HTMLInputElement[];
  expect(switches.map((s) => s.checked)).toEqual([true, true, true, true, true]); // no bigger-team switches here
  fireEvent.click(screen.getByRole("switch", { name: "Careful reviews" }));
  expect(h.onChange).toHaveBeenLastCalledWith({ reviews: false });
  expect(screen.getAllByText("2 rules").length).toBe(5);
});

test("a group's chevron opens its rules, each with a switch that follows the group", () => {
  const h = rules();
  fireEvent.click(screen.getByRole("button", { name: "Careful reviews: each rule" }));
  fireEvent.click(screen.getByRole("switch", { name: "reviews rule a" }));
  const sheet = screen.getByRole("dialog", { name: "Careful reviews" });
  fireEvent.click(sheet.querySelector<HTMLInputElement>("input[aria-label='Careful reviews']")!);
  expect((screen.getByRole("switch", { name: "reviews rule b" }) as HTMLInputElement).disabled).toBe(true);
  expect(h.onChange).toHaveBeenLastCalledWith({ "reviews-a": false, reviews: false });
});

test("the always-on count comes from the list", () => {
  rules();
  expect(document.querySelector(".basics")?.textContent).toMatch(/^Plus 5 that are always on/);
  fireEvent.click(screen.getByRole("button", { name: "See all" }));
  expect(screen.getByRole("dialog", { name: "Always on" }).textContent).toContain("3Basic 3");
});

test("Done returns", () => {
  const h = rules();
  screen.getByText("You can change these any time in Settings.");
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(h.onDone).toHaveBeenCalled();
});
