import type { AssistantApp } from "./bridge";
import useModal from "./useModal";
import "./PhoneGuide.css";

/** Step 18a: reaching your own Claude Code or Codex session from your phone (Paper "v2 — Assistants and phone: phone
 *  guide"). Assieme is installed in those apps from Settings; the session's own remote control does the rest. */
const SESSIONS = [
  {
    app: "claude-code", title: "Claude Code", experimental: false, steps: [
      "In your session on this Mac, type /remote-control",
      "On your phone, open the Claude app, signed in to the same account, and go to Code",
      "Open the session and ask, for example: “What needs me?”",
    ],
  },
  {
    app: "codex", title: "Codex in the ChatGPT app", experimental: true, steps: [
      "In the ChatGPT app on this Mac, turn on remote control for Codex",
      "On your phone, open the ChatGPT app and pick this Mac in Codex",
      "Open your task and ask, for example: “What needs me?”",
    ],
  },
] as const;

export default function PhoneGuide({ apps, onClose }: { apps: AssistantApp[]; onClose: () => void }) {
  const { ref, onKeyDown } = useModal(onClose, []);
  const installed = (app: string) => apps.some((a) => a.app === app && a.installed);
  return (
    <div className="modal-dim">
      <div className="modal phone-guide" role="dialog" aria-modal="true" aria-label="Use your session from your phone"
        tabIndex={-1} ref={ref} onKeyDown={onKeyDown}>
        <div className="modal-head">
          <h2>Use your session from your phone</h2>
          <p className="big">
            Carry on in the Claude Code or Codex session you designed your team in. Assieme is already there, so from
            your phone you can do what you do here. Every change asks you first.
          </p>
        </div>
        {SESSIONS.map((s) => (
          <section key={s.app} className="phone-session">
            <div className="phone-session-head">
              <i className={installed(s.app) ? "tick on" : "tick"} aria-hidden="true" />
              <b>{s.title}</b>
              <span className="mono">{`${installed(s.app) ? "plugin installed" : "not installed"}${s.experimental ? " · experimental" : ""}`}</span>
            </div>
            <ol>
              {s.steps.map((step) => <li key={step}><span>{step}</span></li>)}
            </ol>
          </section>
        ))}
        <section className="phone-awake">
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <path d="M12.5 11.8A5.5 5.5 0 0 1 6.2 5.5a5.5 5.5 0 1 0 6.3 6.3z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
          </svg>
          <div>
            <b>Your phone reaches this Mac only while it’s awake and online</b>
            <p>If it sleeps or loses the internet, your phone loses the session until the Mac is back. Your agents keep their place.</p>
            <p className="small">To keep it awake on power: System Settings → Battery → Options → Prevent automatic sleeping on power adapter when the display is off.</p>
          </div>
        </section>
        <div className="sheet-actions phone-foot">
          <span>Plugins and connectors are in Settings → Your assistants.</span>
          <button type="button" className="btn-primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}
