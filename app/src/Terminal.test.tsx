import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const h = vi.hoisted(() => {
  const state = { webglFails: false };
  const xterms: InstanceType<typeof FakeXterm>[] = [];
  class FakeXterm {
    written: string[] = [];
    addons: unknown[] = [];
    dataHandler: (d: string) => void = () => {};
    cols = 100;
    rows = 30;
    constructor() {
      xterms.push(this);
    }
    open() {}
    loadAddon(a: unknown) {
      this.addons.push(a);
    }
    write(d: string | Uint8Array) {
      this.written.push(typeof d === "string" ? d : new TextDecoder().decode(d));
    }
    onData(f: (d: string) => void) {
      this.dataHandler = f;
      return { dispose() {} };
    }
    dispose() {}
  }
  return { state, xterms, FakeXterm };
});
const xterms = h.xterms;
vi.mock("@xterm/xterm", () => ({ Terminal: h.FakeXterm }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));
vi.mock("@xterm/addon-canvas", () => ({ CanvasAddon: class { kind = "canvas"; } }));
vi.mock("@xterm/addon-webgl", () => ({
  WebglAddon: class {
    kind = "webgl";
    constructor() {
      if (h.state.webglFails) throw new Error("no webgl");
    }
    onContextLoss() {}
    dispose() {}
  },
}));

const handle = { input: vi.fn(), resize: vi.fn(), close: vi.fn() };
let lastOpen: { observe: boolean; onData: (b: Uint8Array) => void; onClose: (w: string) => void } | null = null;
const openTerminal = vi.fn(async (_a: string, _c: number, _r: number, observe: boolean, onData: (b: Uint8Array) => void, onClose: (w: string) => void) => {
  lastOpen = { observe, onData, onClose };
  return handle;
});
const readTerminal = vi.fn(async () => "herdr screen");
vi.mock("./bridge", () => ({
  openTerminal: (...a: Parameters<typeof openTerminal>) => openTerminal(...a),
  readTerminal: (...a: unknown[]) => readTerminal(...(a as [])),
}));

import Terminal from "./Terminal";

const flush = () => act(async () => {});

beforeEach(() => {
  xterms.length = 0;
  h.state.webglFails = false;
  lastOpen = null;
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test("assieme session attaches and writes bytes", async () => {
  render(<Terminal session="assieme" address="t3" />);
  await flush();
  expect(openTerminal).toHaveBeenCalledTimes(1);
  expect(lastOpen!.observe).toBe(true);
  act(() => lastOpen!.onData(new TextEncoder().encode("hello")));
  expect(xterms[0].written.join("")).toContain("hello");
});

test("other session never attaches", async () => {
  render(<Terminal session="default" address="t1" />);
  await flush();
  expect(readTerminal).toHaveBeenCalledWith("default", "t1");
  expect(openTerminal).not.toHaveBeenCalled();
  expect(xterms[0].written.join("")).toContain("herdr screen");
  expect(screen.queryByText("Type here")).toBeNull();
});

test("falls back to canvas when webgl fails", async () => {
  h.state.webglFails = true;
  render(<Terminal session="assieme" address="t3" />);
  await flush();
  expect(xterms[0].addons.some((a) => (a as { kind?: string }).kind === "canvas")).toBe(true);
  expect(screen.getAllByText("Fast drawing is off for this terminal.")).toHaveLength(1);
});

test("closing the component closes the stream", async () => {
  const { unmount } = render(<Terminal session="assieme" address="t3" />);
  await flush();
  unmount();
  expect(handle.close).toHaveBeenCalled();
});

test("type here switches to a typing stream", async () => {
  render(<Terminal session="assieme" address="t3" />);
  await flush();
  fireEvent.click(screen.getByText("Type here"));
  await flush();
  expect(lastOpen!.observe).toBe(false);
  act(() => xterms[xterms.length - 1].dataHandler("x"));
  expect(handle.input).toHaveBeenCalledWith("x");
});

test("reattaches after the stream closes", async () => {
  vi.useFakeTimers();
  render(<Terminal session="assieme" address="t3" />);
  await flush();
  act(() => lastOpen!.onClose("lost"));
  expect(screen.getByText("Reconnecting…")).toBeTruthy();
  await act(async () => {
    vi.advanceTimersByTime(1000);
  });
  expect(openTerminal).toHaveBeenCalledTimes(2);
});
