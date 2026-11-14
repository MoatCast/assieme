import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { AssistantApp } from "./bridge";
import PhoneGuide from "./PhoneGuide";

afterEach(cleanup);

const apps = (code: boolean, codex: boolean): AssistantApp[] => [
  { app: "claude-code", label: "Claude Code", kind: "plugin", available: true, installed: code },
  { app: "claude-app", label: "The Claude app", kind: "connector", available: true, installed: true },
  { app: "codex", label: "Codex in the ChatGPT app", kind: "plugin", available: true, installed: codex },
];

test("the guide, as in Paper", () => {
  render(<PhoneGuide apps={apps(true, true)} onClose={() => {}} />);
  const d = screen.getByRole("dialog", { name: "Use your session from your phone" });
  expect(d).toBeTruthy();
  screen.getByText("Carry on in the Claude Code or Codex session you designed your team in. Assieme is already there, so from your phone you can do what you do here. Every change asks you first.");
  screen.getByText("In your session on this Mac, type /remote-control");
  screen.getByText("On your phone, open the Claude app, signed in to the same account, and go to Code");
  screen.getByText("Open the session and ask, for example: “What needs me?”");
  screen.getByText("In the ChatGPT app on this Mac, turn on remote control for Codex");
  screen.getByText("On your phone, open the ChatGPT app and pick this Mac in Codex");
  screen.getByText("Open your task and ask, for example: “What needs me?”");
  screen.getByText("Your phone reaches this Mac only while it’s awake and online");
  screen.getByText("If it sleeps or loses the internet, your phone loses the session until the Mac is back. Your agents keep their place.");
  screen.getByText("To keep it awake on power: System Settings → Battery → Options → Prevent automatic sleeping on power adapter when the display is off.");
  screen.getByText("Plugins and connectors are in Settings → Your assistants.");
  screen.getByText("plugin installed");
  screen.getByText("plugin installed · experimental");
});

test("an app without the plugin says so", () => {
  render(<PhoneGuide apps={apps(false, false)} onClose={() => {}} />);
  screen.getByText("not installed");
  screen.getByText("not installed · experimental");
});

test("Done and Escape close it", () => {
  const onClose = vi.fn();
  render(<PhoneGuide apps={apps(true, true)} onClose={onClose} />);
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(2);
});
