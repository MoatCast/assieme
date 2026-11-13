import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const b = vi.hoisted(() => ({ setupStatus: vi.fn(), connectAntigravity: vi.fn(), terminalState: vi.fn(), installStop: vi.fn(),
  containerStatus: vi.fn(), containerBuild: vi.fn(), containerOpen: vi.fn(), openLink: vi.fn() }));
vi.mock("./bridge", () => b);
vi.mock("./Terminal", () => ({ default: () => <div /> }));

import AgentMenu from "./AgentMenu";

const flush = () => act(async () => {});
const status = (connected: boolean) => ({ git: true, agents: { antigravity: { installed: true, signed_in: true, connected } } });

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

test("Antigravity before it is connected opens Connect, and is picked only once it is", async () => {
  b.setupStatus.mockResolvedValue(status(false));
  const onChange = vi.fn();
  render(<AgentMenu look="field" value="claude" installed={{ claude: true, antigravity: true }} onChange={onChange} onInstall={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: /Agent: Claude Code/ }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Antigravity/ }));
  await flush();
  screen.getByRole("dialog", { name: "Connect Assieme to Antigravity" });
  expect(onChange).not.toHaveBeenCalled();
});

test("Antigravity already connected is picked straight away", async () => {
  b.setupStatus.mockResolvedValue(status(true));
  const onChange = vi.fn();
  render(<AgentMenu look="field" value="claude" installed={{ claude: true, antigravity: true }} onChange={onChange} onInstall={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: /Agent: Claude Code/ }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Antigravity/ }));
  await flush();
  expect(onChange).toHaveBeenCalledWith({ agent: "antigravity" });
  expect(screen.queryByRole("dialog")).toBeNull();
});
