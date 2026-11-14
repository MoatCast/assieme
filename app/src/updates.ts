import { ask } from "@tauri-apps/plugin-dialog";
import { relaunch } from "@tauri-apps/plugin-process";
import { check } from "@tauri-apps/plugin-updater";

/** A newer signed release on GitHub: ask, then install it and restart. Offline or up to date: nothing. */
export async function offerUpdate() {
  try {
    const update = await check();
    if (!update) return;
    const yes = await ask(`Assieme ${update.version} is ready. Install it and restart Assieme now? Your agents keep running.`,
      { title: "Update Assieme", okLabel: "Install and restart", cancelLabel: "Later" });
    if (!yes) return;
    await update.downloadAndInstall();
    await relaunch();
  } catch {
    // no network or no release yet: try again at the next check
  }
}

/** At start, then once a day: the app stays open in the menu bar for weeks. */
export function watchForUpdates() {
  void offerUpdate();
  return window.setInterval(() => void offerUpdate(), 24 * 3600 * 1000);
}
