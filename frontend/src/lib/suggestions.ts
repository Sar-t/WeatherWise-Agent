/** Suggested first questions for the assistant, from the dashboard's data (no LLM). */

import type { Dashboard } from "./api";

const RAIN_LIKELY = 40; // % chance in an hour
const MAX = 3;

function localDateHour(iso: string, timeZone: string): { date: string; hour: number } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return { date: `${get("year")}-${get("month")}-${get("day")}`, hour: Number(get("hour")) };
}

/**
 * When the first rainy hour falls, in words: this morning (06-12), this afternoon (12-17), this
 * evening (17-21), tonight (21-06, including the small hours after midnight), or tomorrow (from
 * 06:00 on the next day).
 */
export function rainWhen(rainAt: string, now: string, timeZone: string): string {
  const today = localDateHour(now, timeZone).date;
  const { date, hour } = localDateHour(rainAt, timeZone);
  if (date !== today) return hour < 6 ? "tonight" : "tomorrow";
  if (hour < 6 || hour >= 21) return "tonight";
  if (hour >= 17) return "this evening";
  if (hour >= 12) return "this afternoon";
  return "this morning";
}

export function suggestions(data: Dashboard): string[] {
  const out: string[] = [];
  const air = data.air_quality;
  const poorAir = air?.value != null && (air.scale === "naqi" ? air.value >= 201 : air.value >= 4);
  if (poorAir) out.push("Is it safe to go for a walk today?");

  const rainy = data.hourly.find((h) => (h.rain_prob_pct ?? 0) >= RAIN_LIKELY);
  if (rainy) out.push(`Will it rain ${rainWhen(rainy.time, data.local_time, data.location.timezone)}?`);

  for (const fallback of ["Best time for a run tomorrow?", "What should I wear today?"]) {
    if (out.length < MAX) out.push(fallback);
  }
  return out.slice(0, MAX);
}
