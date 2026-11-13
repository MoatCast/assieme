import { useEffect, useState } from "react";

/** Step 31: Settings › General › Musicians. Kept like the theme; anything unknown means Animated. */
export const MUSICIANS_KEY = "assieme.musicians";
export type MusicianMode = "animated" | "still" | "off";
const CHANGED = "assieme-musicians"; // this window: the storage event only reaches the other windows

export function savedMusicianMode(): MusicianMode {
  try {
    const v = localStorage.getItem(MUSICIANS_KEY);
    return v === "still" || v === "off" ? v : "animated";
  } catch {
    return "animated";
  }
}

export function setMusicianMode(m: MusicianMode) {
  try {
    if (m === "animated") localStorage.removeItem(MUSICIANS_KEY);
    else localStorage.setItem(MUSICIANS_KEY, m);
  } catch {
    // storage refused: nothing is kept, and the choice can't apply either
  }
  window.dispatchEvent(new Event(CHANGED));
}

/** The mode now, following a change made here or in another window (main or menu bar panel) at once. */
export function useMusicianMode(): MusicianMode {
  const [mode, setMode] = useState(savedMusicianMode);
  useEffect(() => {
    const follow = () => setMode(savedMusicianMode());
    const fromOther = (e: StorageEvent) => (e.key === MUSICIANS_KEY || e.key === null) && follow();
    window.addEventListener(CHANGED, follow);
    window.addEventListener("storage", fromOther);
    return () => {
      window.removeEventListener(CHANGED, follow);
      window.removeEventListener("storage", fromOther);
    };
  }, []);
  return mode;
}
