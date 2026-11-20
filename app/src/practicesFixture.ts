import type { Practices } from "./bridge";

/** Five groups of two rules, five always-on items and the two bigger-team presets: the shape the brain sends. */
export const PRACTICES: Practices = {
  groups: ["messages", "safety", "reviews", "stopping", "machine"].map((id) => ({
    id, title: { messages: "Fewer, cheaper messages", safety: "Safe by default", reviews: "Careful reviews",
      stopping: "Know when to stop", machine: "Easy on your Mac" }[id]!, summary: `${id} summary`, detail: `${id} detail`,
    rules: ["a", "b"].map((x) => ({ id: `${id}-${x}`, title: `${id} rule ${x}`, detail: "d", text: "t" })),
  })),
  basics: [1, 2, 3, 4, 5].map((i) => ({ title: `Basic ${i}`, detail: "d" })),
  bigger: [{ id: "steward", title: "Steward agent", summary: "s", detail: "d" }, { id: "research-pairs", title: "Research pairs", summary: "r", detail: "d" }],
};
