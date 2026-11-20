import type { RoleDraft } from "./bridge";
import type { StepId } from "./newTeamSteps";

/** New team's answers, kept when the window closes mid-setup so the next New team can offer to continue. */
export type Saved = { machine: string | null; project: string; task: string; roles: RoleDraft[]; chosen: string;
  practices: Record<string, boolean>; at: StepId };
const KEY = "assieme.newteam.draft";

export function saveDraft(s: Saved): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    // storage refused: the draft is simply not kept
  }
}

export function loadDraft(): Saved | null {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null");
    return v && typeof v.project === "string" && v.project && Array.isArray(v.roles) && typeof v.at === "string" ? v as Saved : null;
  } catch {
    return null;
  }
}

export function clearDraft(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // nothing to clear when storage is refused
  }
}
