import { useEffect, useRef, useState } from "react";
import { windowTheme } from "./bridge";

export type Theme = "light" | "dark" | "system";

// Read before the first paint by index.html too; a test keeps the two in step.
export const THEME_KEY = "assieme.theme";

const CHOICES: [Theme, string][] = [["light", "Light"], ["dark", "Dark"], ["system", "Match the Mac"]];

export function savedTheme(): Theme {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

/** Point the tokens and the native window (its buttons, menus and scroll bars) at a theme. No data-theme: follow the Mac. */
export function applyTheme(t: Theme) {
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  void windowTheme(t).catch(() => {}); // only the window chrome: the page is already right
}

/** The other window (main or menu bar panel) shares this storage: follow its change at once. */
export function followTheme() {
  window.addEventListener("storage", (e) => e.key === THEME_KEY && applyTheme(savedTheme()));
}

/** The appearance switch in the top bar. Paper "v2 — Appearance menu open" and "Dark — Appearance menu open". */
export default function Appearance() {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState(savedTheme);
  const wrap = useRef<HTMLDivElement>(null);
  const icon = useRef<HTMLButtonElement>(null);
  const close = () => {
    setOpen(false);
    icon.current?.focus();
  };
  useEffect(() => {
    if (!open) return;
    wrap.current?.querySelector<HTMLElement>("[aria-pressed=true]")?.focus();
    // A press anywhere else closes it and still does what it was for (a tab, a button).
    const away = (e: PointerEvent) => !wrap.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);
  const pick = (t: Theme) => {
    try {
      if (t === "system") localStorage.removeItem(THEME_KEY);
      else localStorage.setItem(THEME_KEY, t);
    } catch {
      // storage refused: the choice still applies until the app restarts
    }
    applyTheme(t);
    setTheme(t);
    close();
  };
  return (
    <div className="appearance" ref={wrap} onKeyDown={(e) => e.key === "Escape" && open && close()}>
      <button type="button" ref={icon} className="gear" aria-label="Appearance" title="Appearance" aria-expanded={open}
        onClick={() => setOpen(!open)}>
        <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
          <circle cx="9" cy="9" r="6.25" fill="none" stroke="var(--quiet-slate)" strokeWidth="1.5" />
          <path d="M9 2.75a6.25 6.25 0 0 1 0 12.5z" fill="var(--quiet-slate)" />
        </svg>
      </button>
      {open && (
        <div className="appearance-menu" role="group" aria-label="Appearance">
          {CHOICES.map(([id, label]) => (
            <button key={id} type="button" aria-pressed={theme === id} onClick={() => pick(id)}>
              <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
                {theme === id && <path d="M3 8.5l3 3 7-7" fill="none" stroke="var(--quiet-ink)" strokeWidth="1.8" strokeLinecap="round" />}
              </svg>
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
