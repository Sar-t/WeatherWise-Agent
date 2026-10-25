/** Plain-language labels and small calculations for the detail tiles, charts and outlook card. */

import type { DayOut, HourOut, OutdoorOut } from "./api";
import { formatHour, formatTemp, formatTime, formatWeekday } from "./format";

/** WHO UV index categories. */
export function uvLabel(uv: number): string {
  const v = Math.round(uv);
  if (v <= 2) return "Low";
  if (v <= 5) return "Moderate";
  if (v <= 7) return "High";
  if (v <= 10) return "Very high";
  return "Extreme";
}

export function humidityLabel(pct: number): string {
  if (pct < 30) return "Dry";
  if (pct <= 60) return "Comfortable";
  return "Humid";
}

export function visibilityLabel(km: number): string {
  if (km >= 10) return "Clear";
  if (km >= 5) return "Good";
  if (km >= 1) return "Reduced";
  return "Poor";
}

/** Wind direction is where it blows from; the arrow points where it's going (0 = up/north). */
export function windArrowRotation(fromDeg: number): number {
  return (fromDeg + 180) % 360;
}

/** How far the sun is between sunrise and sunset, 0..1, or null when it's down. */
export function sunProgress(now: string | Date, sunrise: string, sunset: string): number | null {
  const t = new Date(now).getTime();
  const rise = new Date(sunrise).getTime();
  const set = new Date(sunset).getTime();
  if (t < rise || t > set) return null;
  return (t - rise) / (set - rise);
}

export function dayLength(sunrise: string, sunset: string): string {
  const minutes = Math.round((new Date(sunset).getTime() - new Date(sunrise).getTime()) / 60_000);
  return `${Math.floor(minutes / 60)}\xa0h ${minutes % 60}\xa0min`;
}

export const VERDICT_LABELS: Record<OutdoorOut["verdict"], string> = {
  suitable: "Good time to go out",
  marginal: "Okay, with some care",
  not_suitable: "Better to stay in",
};

/** "18:30 – 20:30" */
export function timeRange(start: string, end: string, timeZone: string): string {
  return `${formatTime(start, timeZone)} – ${formatTime(end, timeZone)}`;
}

/** The text alternative for the hourly chart. */
export function hourlySummary(hours: HourOut[], timeZone: string): string {
  if (!hours.length) return "No hourly forecast available.";
  const hi = hours.reduce((a, b) => (b.temp_c > a.temp_c ? b : a));
  const lo = hours.reduce((a, b) => (b.temp_c < a.temp_c ? b : a));
  const wet = hours.reduce((a, b) => ((b.rain_prob_pct ?? 0) > (a.rain_prob_pct ?? 0) ? b : a));
  const rain = wet.rain_prob_pct ?? 0;
  const rainText =
    rain < 10 ? "Rain unlikely." : `Rain chance peaks at ${Math.round(rain)}% around ${formatHour(wet.time, timeZone)}:00.`;
  return (
    `Next ${hours.length} hours: high ${formatTemp(hi.temp_c)} at ${formatHour(hi.time, timeZone)}:00, ` +
    `low ${formatTemp(lo.temp_c)} at ${formatHour(lo.time, timeZone)}:00. ${rainText}`
  );
}

/** The week's coldest low and warmest high, for drawing every day's range bar on one scale. */
export function weekRange(days: DayOut[]): { min: number; max: number } {
  return {
    min: Math.min(...days.map((d) => d.min_c)),
    max: Math.max(...days.map((d) => d.max_c)),
  };
}

export function dailySummary(days: DayOut[]): string {
  if (!days.length) return "No daily forecast available.";
  const { min, max } = weekRange(days);
  const wettest = days.reduce((a, b) => ((b.rain_prob_pct ?? 0) > (a.rain_prob_pct ?? 0) ? b : a));
  const rain = wettest.rain_prob_pct ?? 0;
  return (
    `Next ${days.length} days: lows from ${formatTemp(min)}, highs up to ${formatTemp(max)}. ` +
    (rain < 20 ? "Mostly dry." : `Best chance of rain ${formatWeekday(wettest.date)}, ${Math.round(rain)}%.`)
  );
}

// Cool blue to soft yellow to warm orange: two stops alone mix to a muddy tan in between.
const TEMP_STOPS: [number, number[]][] = [
  [5, [0x6f, 0xa3, 0xc2]],
  [22, [0xe3, 0xc4, 0x6a]],
  [38, [0xe0, 0x7b, 0x48]],
];

/** A calm cool-to-warm colour for a temperature, for the range bars. */
export function tempColour(celsius: number): string {
  const [first, mid, last] = TEMP_STOPS;
  const [[t0, a], [t1, b]] = celsius <= mid[0] ? [first, mid] : [mid, last];
  const f = Math.min(Math.max((celsius - t0) / (t1 - t0), 0), 1);
  return `#${a.map((c, i) => Math.round(c + (b[i] - c) * f).toString(16).padStart(2, "0")).join("")}`;
}

// The scoring engine's lead-in for a usable block ("mostly good; ..."); the verdict chip already says it.
const CAUTION_PREFIXES = new Set(["mostly good", "possible with care"]);

/** Reasons for the best-time card, without a lead-in that only repeats the verdict. */
export function outlookReasons(reasons: string[]): string[] {
  return reasons.length > 1 && CAUTION_PREFIXES.has(reasons[0].toLowerCase()) ? reasons.slice(1) : reasons;
}

const SKY_GROUPS: Record<string, string> = {
  "clear-day": "clear",
  "clear-night": "clear",
  "partly-cloudy": "partly cloudy",
  "partly-cloudy-night": "partly cloudy",
  cloudy: "cloudy",
  rain: "wet",
  "heavy-rain": "wet",
  drizzle: "wet",
  thunderstorm: "stormy",
  fog: "foggy",
  haze: "hazy",
  snow: "snowy",
  wind: "windy",
};

/**
 * A one-glance summary of the rest of today from the hourly forecast (no LLM), e.g.
 * ["Clear all day", "warmest 31° around 15:00", "no rain expected"]. With under 3 hours of today
 * left it covers the next 24 hours instead.
 */
export function daySummary(hours: HourOut[], now: string, timeZone: string): { label: string; parts: string[] } {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone }).format(new Date(now));
  const todays = hours.filter((h) => new Intl.DateTimeFormat("en-CA", { timeZone }).format(new Date(h.time)) === today);
  const [label, span] = todays.length >= 3 ? ["Today", todays] : ["Next 24 hours", hours];
  if (!span.length) return { label, parts: [] };

  const counts = new Map<string, number>();
  for (const h of span) {
    const group = SKY_GROUPS[h.icon] ?? "cloudy";
    counts.set(group, (counts.get(group) ?? 0) + 1);
  }
  const [group, count] = [...counts].reduce((a, b) => (b[1] > a[1] ? b : a));
  const share = count / span.length;
  const sky = share === 1 ? `${group} ${label === "Today" ? "all day" : "throughout"}` : share >= 0.6 ? `mostly ${group}` : "mixed skies";

  const warmest = span.reduce((a, b) => (b.temp_c > a.temp_c ? b : a));
  const wettest = span.reduce((a, b) => ((b.rain_prob_pct ?? 0) > (a.rain_prob_pct ?? 0) ? b : a));
  const rain = Math.round(wettest.rain_prob_pct ?? 0);
  const rainText =
    rain < 10
      ? "no rain expected"
      : rain < 30
        ? "small chance of rain"
        : `${rain}% chance of rain around ${formatHour(wettest.time, timeZone)}:00`;

  return {
    label,
    parts: [
      sky[0].toUpperCase() + sky.slice(1),
      `warmest ${formatTemp(warmest.temp_c)} around ${formatHour(warmest.time, timeZone)}:00`,
      rainText,
    ],
  };
}
