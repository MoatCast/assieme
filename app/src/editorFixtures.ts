/** Brain answers the editor tests share: the permission table and the role kinds. */
export const TABLE = {
  levels: [
    { id: "safe" as const, title: "Safe", about: "Edit its files, test, commit locally", summary: "Safe: edit its files, run tests, commit locally.", allowed: ["edit_owned", "tests", "commit", "message"], blocked: ["push", "install", "secrets"] },
    { id: "read-only" as const, title: "Read only", about: "For reviewers and researchers", summary: "Read only: read and run tests, never edit.", allowed: ["read", "tests", "message"], blocked: ["push", "edit"] },
    { id: "trusted" as const, title: "Trusted", about: "Also install packages and go online", summary: "Trusted: also install packages and go online. Never push.", allowed: ["edit_owned", "tests", "commit", "message", "install"], blocked: ["push"] },
  ],
  items: {
    ...Object.fromEntries(["edit_owned", "tests", "commit", "message", "read", "edit", "push", "install"].map((k) => [k, { words: `${k} words`, note: k === "push" ? "you merge in Review" : "" }])),
    secrets: { words: "secrets words", note: "claude note", codex_note: "codex note" },
  },
};
export const KINDS = {
  planner: { brief: "Planner brief.", job: "plans", checkout: "read-only" as const },
  builder: { brief: "Builder brief.", job: "builds", checkout: "branch" as const },
  reviewer: { brief: "Reviewer brief.", job: "reviews", checkout: "read-only" as const },
  tester: { brief: "Tester brief.", job: "tests", checkout: "branch" as const },
};
