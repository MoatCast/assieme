import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({ teamBand: vi.fn() }));
vi.mock("./bridge", () => b);
import { AgentFace, assignMusicians, guessSeat } from "./band";

afterEach(cleanup);

test("the rule matches the brain's (tests/test_musicians.py TEAM)", () => {
  const kinds = ["planner", "builder", "builder", "builder", "builder", "reviewer", "tester", null, null];
  expect(assignMusicians(kinds.map((kind) => ({ kind }))).map((s) => s.musician))
    .toEqual(["piano", "violin", "cello", "bass", "violin", "reviewer", "timpani", "flute", "clarinet"]);
  expect(assignMusicians(kinds.map((kind) => ({ kind }))).map((s) => s.palette)).toEqual([0, 0, 0, 0, 1, 0, 0, 0, 0]);
  expect(assignMusicians([{ kind: "builder", musician: "trumpet" }, { kind: "builder", musician: "kazoo" }]).map((s) => s.musician))
    .toEqual(["trumpet", "cello"]);
});

test("a found agent's instrument is guessed from its name", () => {
  expect(guessSeat("builder-2")).toEqual({ musician: "cello", palette: 0 });
  expect(guessSeat("builder-4")).toEqual({ musician: "violin", palette: 1 });
  expect(guessSeat("tester")).toEqual({ musician: "timpani", palette: 0 });
  expect(guessSeat("docs-writer").musician).toBe("flute");
});

test("a saved instrument wins over the guess", async () => {
  b.teamBand.mockResolvedValue({ "builder-2": { musician: "trumpet", palette: 0 } });
  render(<AgentFace team="band-shop" role="builder-2" state="working" />);
  await act(async () => {});
  expect(screen.getByRole("img", { name: "builder-2, trumpet, working" })).toBeTruthy();
});

test("many faces of one team ask the brain once", async () => {
  b.teamBand.mockClear();
  b.teamBand.mockResolvedValue({});
  render(<>{["a", "b", "c", "d"].map((r) => <AgentFace key={r} team="one-question" role={r} state="idle" />)}</>);
  await act(async () => {});
  expect(b.teamBand).toHaveBeenCalledTimes(1);
});
