import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { ToolChoice, ToolItem } from "./bridge";
import ToolPicker, { toolSummary } from "./ToolPicker";

const it = (id: string, locked = false, why = ""): ToolItem => {
  const [type, name] = [id.slice(0, id.indexOf(":")), id.slice(id.indexOf(":") + 1)];
  return { id, type: type as ToolItem["type"], name, description: "", locked, why };
};
const ITEMS: ToolItem[] = [
  it("server:context7"), it("server:local-db"), it("server:browser-mcp"), it("server:paper"),
  it("plugin:planban@planban"), it("plugin:superpowers@x"), it("plugin:runpod@runpod"),
  it("skill:*"), it("skill:pdf", true, 'Claude Code can turn skills off only all together, with "All skills".'),
];
const SUGGESTED = ["server:browser-mcp", "plugin:runpod@runpod"];
const VALUE: ToolChoice = { tools_off: SUGGESTED, tools_new: true };

afterEach(cleanup);

function open(over: Partial<Parameters<typeof ToolPicker>[0]> = {}) {
  const onDone = vi.fn();
  const onClose = vi.fn();
  render(<ToolPicker items={ITEMS} value={VALUE} suggested={SUGGESTED} roleName="reviewer" roleKind="reviewer"
    kindName="Claude Code" onDone={onDone} onClose={onClose} {...over} />);
  return { onDone, onClose };
}
const chip = (name: string) => screen.getByRole("checkbox", { name });

test("chips grouped by type, Assieme's always on, locked skills as one line", () => {
  open();
  for (const h of ["MCP servers", "Plugins", "Skills"]) expect(screen.getByText(h)).toBeTruthy();
  expect(screen.getByText("Assieme’s team tools: always on")).toBeTruthy();
  expect(screen.getByText('Claude Code can turn skills off only all together, with "All skills".')).toBeTruthy();
  expect(screen.queryByRole("checkbox", { name: "pdf" })).toBeNull();
  expect(screen.getByText("6 of 8")).toBeTruthy();
  expect(screen.getByText("Suggested for a reviewer: browser and deploy tools are off.")).toBeTruthy();
});

test("toggling changes the count and the suggestion note", () => {
  open();
  fireEvent.click(chip("browser-mcp"));
  expect(chip("browser-mcp").getAttribute("aria-checked")).toBe("true");
  expect(screen.getByText("7 of 8")).toBeTruthy();
  expect(screen.getByText("You changed this from the reviewer suggestion.")).toBeTruthy();
  fireEvent.click(screen.getByText("Use suggestion"));
  expect(screen.getByText("6 of 8")).toBeTruthy();
});

test("search filters, the count stays the whole total", () => {
  open();
  fireEvent.change(screen.getByPlaceholderText("Search skills and servers"), { target: { value: "pa" } });
  expect(screen.queryByRole("checkbox", { name: "context7" })).toBeNull();
  expect(chip("paper")).toBeTruthy();
  expect(screen.getByText("6 of 8")).toBeTruthy();
  expect(screen.getByText("1 of 8 match “pa”.")).toBeTruthy();
});

test("Select all and Clear work on what is shown and skip locked", () => {
  const { onDone } = open();
  fireEvent.click(screen.getByText("Clear"));
  expect(screen.getByText("0 of 8")).toBeTruthy();
  fireEvent.click(screen.getByText("Select all"));
  expect(screen.getByText("8 of 8")).toBeTruthy();
  fireEvent.click(screen.getByText("Done"));
  expect(onDone).toHaveBeenCalledWith({ tools_off: [], tools_new: true });
});

test("a locked chip shows its sentence and can't be toggled", () => {
  const cursor = [it("server:proj-db", true, "Cursor reads its servers from the project folder, so every Cursor agent working there gets the same ones.")];
  open({ items: cursor, suggested: [], value: { tools_off: [], tools_new: true }, kindName: "Cursor" });
  const locked = screen.getByText("proj-db").closest("[aria-disabled]");
  expect(locked?.getAttribute("aria-disabled")).toBe("true");
  expect(screen.getByText(/Cursor reads its servers from the project folder/)).toBeTruthy();
  expect(screen.getByText("0 of 0")).toBeTruthy();
});

test("the new-ones switch and Done send the choice; Cancel and Escape send nothing", () => {
  const { onDone, onClose } = open();
  fireEvent.click(screen.getByRole("switch", { name: "Add newly installed ones" }));
  fireEvent.click(screen.getByText("Done"));
  expect(onDone).toHaveBeenCalledWith({ tools_off: SUGGESTED, tools_new: false });
  cleanup();
  const second = open();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(second.onClose).toHaveBeenCalled();
  expect(second.onDone).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
});

test("120 items render and search finds the last one", () => {  // Review Focus 4
  const many = Array.from({ length: 120 }, (_, i) => it(`skill:skill-${i}`));
  open({ items: many, suggested: [], value: { tools_off: [], tools_new: true }, kindName: "Codex" });
  expect(screen.getByText("120 of 120")).toBeTruthy();
  fireEvent.change(screen.getByPlaceholderText("Search skills and servers"), { target: { value: "skill-119" } });
  expect(within(screen.getByRole("dialog")).getByRole("checkbox", { name: "skill-119" })).toBeTruthy();
});

test("a plugin shows its own name, without the marketplace after @", () => {
  open();
  expect(chip("planban")).toBeTruthy();
  expect(screen.queryByText("planban@planban")).toBeNull();
});

test("toolSummary", () => {
  expect(toolSummary(ITEMS, VALUE)).toBe("6 of 8");
  expect(toolSummary(ITEMS, { tools_off: [], tools_new: true })).toBe("8 of 8");
  expect(toolSummary([], { tools_off: [], tools_new: true })).toBe("");
});
