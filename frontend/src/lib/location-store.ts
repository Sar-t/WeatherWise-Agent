/** The last location the user looked at, remembered for the next visit. */

import type { Place } from "./api";

const KEY = "ww.lastPlace";

export function loadPlace(): Place | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Place;
    if (value.kind === "coords" && Number.isFinite(value.lat) && Number.isFinite(value.lon)) return value;
    if (value.kind === "place" && typeof value.name === "string" && value.name.trim()) return value;
  } catch {
    // unreadable or blocked: treat as a first visit
  }
  return null;
}

export function savePlace(place: Place): void {
  try {
    const stored: Place =
      place.kind === "coords"
        ? { kind: "coords", lat: Math.round(place.lat * 100) / 100, lon: Math.round(place.lon * 100) / 100 }
        : place;
    window.localStorage.setItem(KEY, JSON.stringify(stored));
  } catch {
    // storage blocked: nothing to remember
  }
}
