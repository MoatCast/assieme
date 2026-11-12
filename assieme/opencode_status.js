// Assieme's status plugin for OpenCode: its session state -> assieme.hook, which reports it on the agent's terminal.
// ASSIEME_HOOK is the hook's command line as a JSON array; without it the plugin does nothing.
import { spawn } from "node:child_process";

export const AssiemeStatus = async () => {
  let hook = null;
  try { hook = JSON.parse(process.env.ASSIEME_HOOK || "null"); } catch {}
  const say = (state) => {
    if (!Array.isArray(hook) || !state) return;
    try { spawn(hook[0], [...hook.slice(1), state], { stdio: "ignore" }).on("error", () => {}); } catch {}
  };
  say("idle"); // started and waiting
  return {
    event: async ({ event }) => {
      const t = event?.type;
      if (t === "session.idle") say("idle");
      else if (t === "permission.asked" || t === "permission.updated") say("blocked");
      else if (t === "session.status") {
        const s = event.properties?.status?.type;
        say(s === "busy" || s === "retry" ? "working" : s === "idle" ? "idle" : null);
      }
    },
  };
};
