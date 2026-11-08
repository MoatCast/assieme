import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  menuFit, menuState, notificationsPause, notificationsResume, onMenuOpened, paneAct, windowOpen,
  type MenuButton, type MenuItem, type MenuState, type MenuTeam,
} from "./bridge";
import { ago } from "./Terminals";
import { tokens } from "./Team";
import "./tokens.css";
import "./MenuBar.css";

const OPEN: MenuButton = { id: "open", label: "Open", kind: "reply" };

function Item({ team, item, press }: { team: string; item: MenuItem; press: (t: string, i: MenuItem, b: MenuButton) => void }) {
  const buttons = item.buttons.length ? item.buttons : [OPEN]; // an agent Assieme can only watch: you can still go and look
  if (item.state === "asking") {
    return (
      <div className="mb-ask">
        <div className="mb-ask-text">
          <span className="mb-role">{item.role}</span> <span className="mb-says">asks:</span> “{item.text}”
        </div>
        <div className="mb-buttons">
          {buttons.map((b, i) => (
            <button key={b.id} type="button" className={i === 0 ? "mb-pill on" : "mb-pill"} onClick={() => press(team, item, b)}>{b.label}</button>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="mb-row">
      <span className="mb-dot need" />
      <span className="mb-grow"><span className="mb-role">{item.role}</span><span className="mb-says"> {[item.says, ago(item.since_s)].filter(Boolean).join(" · ")}</span></span>
      <button type="button" className="mb-text" onClick={() => press(team, item, buttons[0])}>{buttons[0].label}</button>
    </div>
  );
}

function Team({ team, press }: { team: MenuTeam; press: (t: string, i: MenuItem, b: MenuButton) => void }) {
  if (!team.need) {
    return (
      <div className="mb-row quiet">
        <span className="mb-dot" />
        <span className="mb-grow mb-mute"><span className="mb-name">{team.name}</span> · all quiet</span>
      </div>
    );
  }
  return (
    <>
      <div className="mb-head">
        <span className="mb-title">{team.name}</span>
        <span className="mb-mute">· {team.where}</span>
        <span className="mb-grow" />
        <span className="mb-need">{team.need} need{team.need === 1 ? "s" : ""} you</span>
      </div>
      {team.items.map((i) => <Item key={i.role} team={team.name} item={i} press={press} />)}
    </>
  );
}

function today(m: MenuState) {
  const fixes = m.auto_fixes === 1 ? "1 fix runs automatically" : m.auto_fixes ? `${m.auto_fixes} fixes run automatically` : "";
  const spend = m.spend_tokens === null ? "" : m.spend_tokens ? `Today: ${tokens(m.spend_tokens)} tokens` : "No tokens used yet today";
  return [spend, fixes].filter(Boolean).join(" · ");
}

/** The panel under the menu bar icon (Paper "v2 — Menu bar and notifications"). */
export default function MenuBar({ pollMs = 5000 }: { pollMs?: number }) {
  const [m, setM] = useState<MenuState | null>(null);
  const [note, setNote] = useState("");
  const [off, setOff] = useState(false);
  const panel = useRef<HTMLDivElement>(null);

  const refresh = useCallback(() => {
    menuState().then((got) => { setM(got); setOff(false); }).catch(() => setOff(true));
  }, []);

  useEffect(() => {
    document.documentElement.classList.add("menu-window");
    refresh();
    const stop = onMenuOpened(() => { setNote(""); refresh(); });
    const t = setInterval(() => { if (document.hasFocus()) refresh(); }, pollMs);
    return () => { clearInterval(t); void stop.then((f) => f()); };
  }, [pollMs, refresh]);

  useLayoutEffect(() => {
    if (panel.current) void menuFit(panel.current.scrollHeight).catch(() => {});
  });

  const press = async (team: string, item: MenuItem, b: MenuButton) => {
    if (b.kind === "reply" || !item.fix) return windowOpen({ team, role: item.role, view: "terminals" });
    setNote("");
    try {
      const r = await paneAct({ team, role: item.role, fix: item.fix, button: b.id });
      if (!r.ok) setNote(r.detail);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    }
    refresh();
  };

  const pause = async (run: () => Promise<MenuState>) => {
    try { setM(await run()); } catch { setOff(true); }
  };

  const line = m && today(m);
  return (
    <div className="mb-panel" ref={panel}>
      {off && <div className="mb-row mb-mute">Assieme’s background service isn’t answering. Open Assieme to fix it.</div>}
      {m && !m.teams.length && <div className="mb-row mb-mute">No teams running.</div>}
      {m && m.teams.filter((t) => t.need).map((t) => <Team key={t.name} team={t} press={press} />)}
      {m && m.count > 0 && m.teams.some((t) => !t.need) && <div className="mb-line" />}
      {m && m.teams.filter((t) => !t.need).map((t) => <Team key={t.name} team={t} press={press} />)}
      {note && <div className="mb-row mb-note" role="alert">{note}</div>}
      {m && m.teams.length > 0 && <div className="mb-line" />}
      <button type="button" className="mb-item" onClick={() => void windowOpen()}>Open Assieme</button>
      {m?.paused_until
        ? <button type="button" className="mb-item" onClick={() => void pause(notificationsResume)}>Paused until {m.paused_until} · Resume</button>
        : <button type="button" className="mb-item" disabled={!m} onClick={() => void pause(notificationsPause)}>Pause notifications for 1 hour</button>}
      {line && <div className="mb-foot">{line}</div>}
    </div>
  );
}
