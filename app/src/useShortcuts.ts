// The main window's keys (spec "Keyboard and command palette"). ⌘K works everywhere, even in a text field or a
// terminal; the others only while neither has focus, so ⌘3 in the reply field stays the field's. A sheet open on top
// keeps its keys.
import { useEffect, useRef } from "react";

export type Shortcuts = { palette(): void; agent(i: number): void; needsYou(): void; newTeam(): void; settings(): void };

/** A text field, a menu or a terminal (xterm types through a hidden textarea) has focus. */
export function typing(el: Element | null) {
  if (!el) return false;
  return el.matches("input, textarea, select, [contenteditable=''], [contenteditable='true']") || !!el.closest("[data-terminal]");
}

export function useShortcuts(handlers: Shortcuts, enabled = true) {
  const h = useRef(handlers);
  h.current = handlers;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (!e.metaKey || e.altKey || e.ctrlKey || e.shiftKey || document.querySelector("[aria-modal='true']")) return;
      const key = e.key.toLowerCase();
      const run = (f: () => void) => { e.preventDefault(); e.stopPropagation(); f(); };
      if (key === "k") return run(() => h.current.palette());
      if (typing(document.activeElement)) return;
      if (/^[1-9]$/.test(key)) return run(() => h.current.agent(Number(key) - 1));
      if (key === "j") return run(() => h.current.needsYou());
      if (key === "n") return run(() => h.current.newTeam());
      if (key === ",") return run(() => h.current.settings());
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [enabled]);
}
