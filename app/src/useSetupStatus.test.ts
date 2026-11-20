import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { SetupStatus } from "./bridge";

const b = vi.hoisted(() => ({ setupStatus: vi.fn() }));
vi.mock("./bridge", () => b);

import { useSetupStatus } from "./useSetupStatus";

const status: SetupStatus = { git: true, agents: { claude: { installed: true, signed_in: true } } };
beforeEach(() => { vi.useFakeTimers(); b.setupStatus.mockReset(); });
afterEach(() => vi.useRealTimers());

test("status isn't polled while it is switched off", async () => {
  b.setupStatus.mockResolvedValue(status);
  renderHook(() => useSetupStatus(false));
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(b.setupStatus).not.toHaveBeenCalled();
});

test("status is polled every 3 seconds, one call at a time, and stops on unmount", async () => {
  b.setupStatus.mockResolvedValue(status);
  const { result, unmount } = renderHook(() => useSetupStatus(true));
  await act(async () => {});
  expect(result.current.status).toEqual(status);
  expect(b.setupStatus).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(b.setupStatus).toHaveBeenCalledTimes(2);
  b.setupStatus.mockReturnValue(new Promise(() => {}));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(b.setupStatus).toHaveBeenCalledTimes(3);
  unmount();
  b.setupStatus.mockResolvedValue(status);
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(b.setupStatus).toHaveBeenCalledTimes(3);
});

test("a failed check says why", async () => {
  b.setupStatus.mockRejectedValue(new Error("brain away"));
  const { result } = renderHook(() => useSetupStatus(true));
  await act(async () => {});
  expect(result.current.error).toBe("brain away");
});
