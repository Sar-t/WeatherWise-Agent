/** Display formatting. Times are always shown in the location's own timezone, not the browser's. */

export function formatTime(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone }).format(
    new Date(iso),
  );
}

export function formatHour(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-IN", { hour: "numeric", hourCycle: "h23", timeZone })
    .format(new Date(iso))
    .padStart(2, "0");
}

export function formatWeekday(isoDate: string, timeZone: string): string {
  // A calendar date ("2026-10-24") is local to the place: read it at local noon to avoid day shifts.
  return new Intl.DateTimeFormat("en-IN", { weekday: "short", timeZone }).format(new Date(`${isoDate}T12:00:00Z`));
}

export function formatDateTime(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-IN", {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).format(new Date(iso));
}

/** "Sat 24 Oct" */
export function formatDay(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone }).format(
    new Date(iso),
  );
}

/** Whole degrees with a real minus sign: -3 -> "−3°". */
export function formatTemp(celsius: number): string {
  const value = Math.round(celsius);
  return `${value < 0 ? "−" : ""}${Math.abs(value)}°`;
}

export function formatPercent(value: number | null | undefined): string {
  return value == null ? "–" : `${Math.round(value)}%`;
}

/** The current wall-clock time at a place, e.g. for a ticking header clock. */
export function localClock(timeZone: string, now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone }).format(now);
}

/** "City, State, Country" -> a short secondary line without repeating the name. */
export function secondaryPlaceLine(name: string, formatted: string): string {
  const parts = formatted.split(",").map((p) => p.trim()).filter(Boolean);
  const isCode = (p: string) => /^[A-Z]{2,3}$/.test(p); // state codes like "RJ", "IDF" read as noise
  const named = name.toLowerCase().split(",").map((p) => p.trim());
  // Drop what the name already says, including longer forms ("United States of America").
  const repeats = (p: string) => named.some((n) => p.toLowerCase().includes(n) || n.includes(p.toLowerCase()));
  return parts.filter((p) => !repeats(p) && !isCode(p)).join(", ");
}
