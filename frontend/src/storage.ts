// Lightweight localStorage persistence for UI settings and frequency bookmarks.

const SETTINGS_KEY = "cascade-sdr-settings";
const BOOKMARKS_KEY = "cascade-sdr-bookmarks";

// One-time migration from the old "SDR-Ultra" keys so saved settings/bookmarks
// survive the rename.
(function migrate() {
  try {
    for (const [oldKey, newKey] of [
      ["sdr-ultra-settings", SETTINGS_KEY],
      ["sdr-ultra-bookmarks", BOOKMARKS_KEY],
    ]) {
      const old = localStorage.getItem(oldKey);
      if (old !== null && localStorage.getItem(newKey) === null) {
        localStorage.setItem(newKey, old);
        localStorage.removeItem(oldKey);
      }
    }
  } catch {
    /* storage disabled — ignore */
  }
})();

export type Settings = Record<string, string | number | boolean>;

export interface Bookmark {
  name: string;
  mhz: number;
  demod?: string;
}

export function loadSettings(): Settings {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
  } catch {
    return {};
  }
}

export function saveSettings(s: Settings): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
  } catch {
    /* storage full / disabled — ignore */
  }
}

export function loadBookmarks(): Bookmark[] {
  try {
    const v = JSON.parse(localStorage.getItem(BOOKMARKS_KEY) || "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

export function saveBookmarks(list: Bookmark[]): void {
  try {
    localStorage.setItem(BOOKMARKS_KEY, JSON.stringify(list));
  } catch {
    /* ignore */
  }
}
