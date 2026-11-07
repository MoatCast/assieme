import { beforeEach, expect, test, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...a: unknown[]) => invoke(...a),
  Channel: class {
    onmessage: (m: unknown) => void = () => {};
  },
}));

import * as bridge from "./bridge";

beforeEach(() => invoke.mockReset());

test("snapshot passes method name", async () => {
  invoke.mockResolvedValue({ teams: [] });
  await bridge.snapshot();
  expect(invoke).toHaveBeenCalledWith("brain_call", { method: "brain.snapshot", params: {} });
});

test("openTerminal streams bytes and routes input, resize and close to its id", async () => {
  invoke.mockResolvedValueOnce(7);
  const got: Uint8Array[] = [];
  const h = await bridge.openTerminal("t3", 100, 30, true, (b) => got.push(b), () => {});
  const args = invoke.mock.calls[0][1];
  expect(invoke.mock.calls[0][0]).toBe("terminal_open");
  expect([args.address, args.cols, args.rows, args.observe]).toEqual(["t3", 100, 30, true]);
  args.onData.onmessage(new Uint8Array([104, 105]).buffer);
  expect(Array.from(got[0])).toEqual([104, 105]);
  await h.input("hi");
  expect(invoke).toHaveBeenLastCalledWith("terminal_input", { id: 7, data: [104, 105] });
  await h.resize(120, 40);
  expect(invoke).toHaveBeenLastCalledWith("terminal_resize", { id: 7, cols: 120, rows: 40 });
  await h.close();
  expect(invoke).toHaveBeenLastCalledWith("terminal_close", { id: 7 });
});

test("readTerminal names the session", async () => {
  invoke.mockResolvedValue("text");
  expect(await bridge.readTerminal("default", "t1")).toBe("text");
  expect(invoke).toHaveBeenCalledWith("terminal_read", { session: "default", address: "t1" });
});
