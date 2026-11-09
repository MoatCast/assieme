import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { SetupStatus } from "./bridge";

const b = vi.hoisted(() => ({ setupStatus: vi.fn(), installTools: vi.fn() }));
vi.mock("./bridge", () => b);
vi.mock("./InstallSheet", () => ({
  default: ({ agent, start, onClose }: { agent: string; start: string; onClose: () => void }) => (
    <div><span>{`sheet ${agent} at ${start}`}</span><button type="button" onClick={onClose}>close sheet</button></div>
  ),
}));

import NewUserChecklist from "./NewUserChecklist";

const NONE = { installed: false, signed_in: false };
const status = (over: Partial<{ git: boolean; claude: { installed: boolean; signed_in: boolean }; codex: { installed: boolean; signed_in: boolean } }> = {}): SetupStatus => ({
  git: over.git ?? true, agents: { claude: over.claude ?? NONE, codex: over.codex ?? NONE },
});
const flush = () => act(async () => {});
const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

const show = async (st: SetupStatus | Error | "pending", props: Partial<React.ComponentProps<typeof NewUserChecklist>> = {}) => {
  if (st === "pending") b.setupStatus.mockReturnValue(new Promise(() => {}));
  else if (st instanceof Error) b.setupStatus.mockRejectedValue(st);
  else b.setupStatus.mockResolvedValue(st);
  const r = render(<NewUserChecklist herdr={false} onStart={vi.fn()} onOpenSettings={vi.fn()} {...props} />);
  await flush();
  return r;
};

beforeEach(() => {
  vi.useFakeTimers();
  b.setupStatus.mockReset();
  b.installTools.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test("new user: intro, tools installed, both installs and the plans line", async () => {
  await show(status());
  screen.getByText("Welcome to Assieme");
  screen.getByRole("heading", { name: "Let’s start your first team." });
  screen.getByText("Assieme runs coding agents for you and tells you when one needs you. Setup may take a while, mostly downloads, and you won’t need to type any commands.");
  screen.getByText("Developer tools");
  screen.getByText("Installed");
  screen.getByText("An agent to do the work");
  screen.getByText("Pick the one you pay for. Claude Code needs a paid Claude plan. Codex works with ChatGPT, with limits on the free plan.");
  button("Install Claude Code");
  button("Install Codex");
  expect(button("Start your first team").disabled).toBe(true);
  screen.getByText("after you’ve added an agent");
  screen.getByText("What happens to your project");
  screen.getByText("Each agent works on its own copy");
  screen.getByText("You watch it live");
  screen.getByText("You decide what to keep");
});

test("no developer tools: Apple's tools row, and it doesn't block starting", async () => {
  const onStart = vi.fn();
  await show(status({ git: false, claude: { installed: true, signed_in: true } }), { onStart });
  screen.getByText("Apple’s free Command Line Tools, which track your project’s changes. This opens Apple’s installer, a large download. You can install an agent meanwhile.");
  button("Install developer tools");
  expect(button("Start your first team").disabled).toBe(false);
  fireEvent.click(button("Start your first team"));
  expect(onStart).toHaveBeenCalledTimes(1);
});

test("Install developer tools opens Apple's installer, then waits and can open it again", async () => {
  b.installTools.mockResolvedValue({ ok: true });
  await show(status({ git: false }));
  fireEvent.click(button("Install developer tools"));
  await flush();
  expect(b.installTools).toHaveBeenCalledTimes(1);
  screen.getByText("Waiting for Apple’s installer… Finish there, and this will update by itself.");
  expect(screen.queryByRole("button", { name: "Install developer tools" })).toBeNull();
  fireEvent.click(button("Open it again"));
  await flush();
  expect(b.installTools).toHaveBeenCalledTimes(2);
  b.setupStatus.mockResolvedValue(status({ git: true }));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.queryByText(/Waiting for Apple/)).toBeNull();
  screen.getByText("Installed");
});

test("a failed Install developer tools shows next to the button and can be retried", async () => {
  b.installTools.mockRejectedValueOnce(new Error("xcode-select missing")).mockResolvedValue({ ok: true });
  await show(status({ git: false }));
  fireEvent.click(button("Install developer tools"));
  await flush();
  screen.getByText(/xcode-select missing/);
  expect(screen.queryByText(/Waiting for Apple/)).toBeNull();
  fireEvent.click(button("Install developer tools"));
  await flush();
  screen.getByText(/Waiting for Apple/);
  expect(screen.queryByText(/xcode-select missing/)).toBeNull();
});

test("installed but not signed in: Sign in opens the sheet at sign-in", async () => {
  await show(status({ claude: { installed: true, signed_in: false } }));
  screen.getByText("Claude Code");
  screen.getByText("Installed, not signed in");
  expect(button("Start your first team").disabled).toBe(true);
  screen.getByText("after you’ve signed in");
  fireEvent.click(button("Sign in"));
  screen.getByText("sheet claude at signin");
  fireEvent.click(button("close sheet"));
  expect(screen.queryByText(/^sheet /)).toBeNull();
});

test("Install Claude Code opens the sheet at its confirmation", async () => {
  await show(status());
  fireEvent.click(button("Install Codex"));
  screen.getByText("sheet codex at install");
});

test("agent ready: Installed, and Start your first team is enabled and opens New team", async () => {
  const onStart = vi.fn();
  await show(status({ claude: { installed: true, signed_in: true } }), { onStart });
  const row = screen.getByText("Claude Code").closest(".check-row") as HTMLElement;
  within(row).getByText("Installed");
  screen.getByText("on a project you choose");
  expect(button("Start your first team").disabled).toBe(false);
  fireEvent.click(button("Start your first team"));
  expect(onStart).toHaveBeenCalled();
});

test("an unclear status doesn't lock anyone out", async () => {
  // The brain reports signed_in true when a status command can't run or gives an unknown result.
  await show(status({ codex: { installed: true, signed_in: true } }));
  expect(button("Start your first team").disabled).toBe(false);
});

test("if the setup check itself fails, it says so and doesn't block starting", async () => {
  await show(new Error("brain said no"));
  screen.getByText(/brain said no/);
  expect(button("Start your first team").disabled).toBe(false);
});

test("before the first status answer it claims nothing is missing", async () => {
  await show("pending");
  expect(screen.queryByRole("button", { name: /Install/ })).toBeNull();
  expect(screen.queryByText("An agent to do the work")).toBeNull();
  expect(button("Start your first team").disabled).toBe(true);
});

test("the herdr row shows only if herdr is installed", async () => {
  const { unmount } = await show(status());
  expect(screen.queryByText("herdr")).toBeNull();
  unmount();
  await show(status(), { herdr: true });
  screen.getByText("herdr");
});

test("Privacy in the footnote opens Settings at Privacy", async () => {
  const onOpenSettings = vi.fn();
  await show(status(), { onOpenSettings });
  screen.getByText(/Assieme runs on this Mac and has no server of its own\./);
  fireEvent.click(button("Settings, Privacy"));
  expect(onOpenSettings).toHaveBeenCalledWith("privacy");
});

test("status is polled every 3 seconds, one call at a time, and stops on unmount", async () => {
  const { unmount } = await show(status());
  expect(b.setupStatus).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(b.setupStatus).toHaveBeenCalledTimes(2);
  b.setupStatus.mockReturnValue(new Promise(() => {}));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(b.setupStatus).toHaveBeenCalledTimes(3);
  unmount();
  b.setupStatus.mockResolvedValue(status());
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(b.setupStatus).toHaveBeenCalledTimes(3);
});
