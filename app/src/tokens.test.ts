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

test("musicians: the second colours differ from the first in light and dark", async () => { // step 31, Review Focus 1
  // @ts-expect-error type error without @types/node package, as in vite.config.ts
  const { readFileSync } = (await import("node:fs")) as { readFileSync: (f: string, e: string) => string };
  const css = readFileSync("src/tokens.css", "utf8");
  const pair = (name: string) => css.match(new RegExp(`${name}: var\\(--light, (#[0-9a-f]+)\\) var\\(--dark, (#[0-9a-f]+)\\)`))!.slice(1);
  const [m, c] = [pair("--quiet-musician-m"), pair("--quiet-musician-c")];
  expect(m[0]).not.toBe(c[0]);
  expect(m[1]).not.toBe(c[1]);
  for (const k of "dlmckbwe") expect(pair(`--quiet-musician-${k}`)).toHaveLength(2);
  expect(pair("--quiet-musician-grey")).toHaveLength(2);
});
