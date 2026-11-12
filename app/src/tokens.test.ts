import { expect, test } from "vitest";

// light-dark() needs WebKit 17.5: on an older Mac every colour token would be invalid, light mode included.
test("tokens.css uses no light-dark(), and every dark colour has a light one", async () => {
  // @ts-expect-error type error without @types/node package, as in vite.config.ts
  const { readFileSync } = (await import("node:fs")) as { readFileSync: (f: string, e: string) => string };
  const css = readFileSync("src/tokens.css", "utf8");
  expect(css).not.toContain("light-dark(");
  const paired = [...css.matchAll(/^\s*(--quiet-[\w-]+):\s*var\(--light, ([^)]+)\) var\(--dark, ([^)]+)\);/gm)];
  expect(paired.length).toBeGreaterThan(30);
  for (const [, , light, dark] of paired) expect([light, dark].every((c) => /^#[0-9a-f]{6,8}$/.test(c))).toBe(true);
});
