import { describe, expect, it } from "vitest";
import delhi from "@/test/fixtures/dashboard-delhi.json";
import type { Dashboard, HourOut } from "./api";
import {
  dayLength,
  daySummary,
  hourlySummary,
  outlookReasons,
  sunProgress,
  tempColour,
  uvLabel,
  visibilityLabel,
  weekRange,
  windArrowRotation,
} from "./details";

const data = delhi as Dashboard;

describe("labels", () => {
  it("uses the WHO UV categories", () => {
    expect([0, 2, 3, 5, 6, 7, 8, 10, 11].map(uvLabel)).toEqual([
      "Low", "Low", "Moderate", "Moderate", "High", "High", "Very high", "Very high", "Extreme",
    ]);
    expect(uvLabel(2.4)).toBe("Low");
    expect(uvLabel(2.6)).toBe("Moderate");
  });

  it("describes visibility", () => {
    expect(visibilityLabel(10)).toBe("Clear");
    expect(visibilityLabel(0.4)).toBe("Poor");
  });
});

describe("wind and sun", () => {
  it("points the arrow where the wind goes", () => {
    expect(windArrowRotation(0)).toBe(180); // a north wind blows south
    expect(windArrowRotation(270)).toBe(90);
  });

  it("places the sun between sunrise and sunset, or nowhere at night", () => {
    const rise = "2026-10-25T06:00:00+05:30";
    const set = "2026-10-25T18:00:00+05:30";
    expect(sunProgress("2026-10-25T12:00:00+05:30", rise, set)).toBe(0.5);
    expect(sunProgress("2026-10-25T20:00:00+05:30", rise, set)).toBeNull();
    expect(dayLength(rise, "2026-10-25T17:42:00+05:30")).toBe("11\xa0h 42\xa0min");
  });
});

describe("chart text summaries (real Delhi response)", () => {
  it("names the high, the low and the rain outlook", () => {
    const text = hourlySummary(data.hourly, data.location.timezone);
    const temps = data.hourly.map((h) => h.temp_c);
    expect(text).toContain(`high ${Math.max(...temps)}°`);
    expect(text).toContain(`low ${Math.min(...temps)}°`);
    expect(text).toMatch(/Rain unlikely\.|Rain chance peaks/);
  });

  it("puts every day on one scale", () => {
    const { min, max } = weekRange(data.daily);
    expect(data.daily.every((d) => d.min_c >= min && d.max_c <= max)).toBe(true);
  });
});

it("colours cold blue and hot orange", () => {
  expect(tempColour(-10)).toBe("#6fa3c2");
  expect(tempColour(45)).toBe("#e07b48");
});

describe("outlookReasons", () => {
  it("drops the lead-in that repeats the verdict when there's more to say", () => {
    expect(outlookReasons(["mostly good", "NAQI 137, Moderately Polluted around 19:30"])).toEqual([
      "NAQI 137, Moderately Polluted around 19:30",
    ]);
    expect(outlookReasons(["possible with care", "gusts 40 km/h around 16:00"])).toEqual(["gusts 40 km/h around 16:00"]);
  });

  it("keeps everything else", () => {
    expect(outlookReasons(["mostly good"])).toEqual(["mostly good"]);
    expect(outlookReasons(["Weather alert: Moderate rain-flood warning", "Gusts 48 km/h"])).toHaveLength(2);
  });
});

describe("daySummary", () => {
  it("summarises the rest of today from the real Delhi response", () => {
    const { label, parts } = daySummary(data.hourly, data.local_time, data.location.timezone);
    expect(label).toBe("Today");
    const today = data.hourly.filter((h) => h.time.slice(0, 10) === data.local_time.slice(0, 10));
    const warmest = Math.max(...today.map((h) => h.temp_c));
    expect(parts).toHaveLength(3);
    expect(parts[1]).toContain(`warmest ${warmest}°`);
    expect(parts[1]).toMatch(/around \d\d:00$/);
  });

  const hour = (time: string, icon: string, temp: number, rain: number) =>
    ({ time, icon, temp_c: temp, feels_like_c: temp, rain_prob_pct: rain, wind_kmh: 5 }) as HourOut;

  it("words a clear day, a mixed one and a wet one", () => {
    const tz = "Asia/Kolkata";
    const now = "2026-10-25T13:06:00+05:30";
    const clear = ["13", "14", "15", "16"].map((h, i) => hour(`2026-10-25T${h}:30:00+05:30`, "clear-day", 29 + i, 0));
    expect(daySummary(clear, now, tz).parts).toEqual(["Clear all day", "warmest 32° around 16:00", "no rain expected"]);

    const wet = [hour("2026-10-25T13:30:00+05:30", "cloudy", 25, 20), hour("2026-10-25T14:30:00+05:30", "rain", 24, 70),
      hour("2026-10-25T15:30:00+05:30", "clear-day", 26, 0)];
    expect(daySummary(wet, now, tz).parts).toEqual(["Mixed skies", "warmest 26° around 15:00", "70% chance of rain around 14:00"]);
  });

  it("looks at the next 24 hours late in the evening", () => {
    const hours = ["23", "00", "01"].map((h, i) =>
      hour(`2026-10-${i ? "26" : "25"}T${h}:30:00+05:30`, "clear-night", 20, 0));
    expect(daySummary(hours, "2026-10-25T22:40:00+05:30", "Asia/Kolkata").label).toBe("Next 24 hours");
  });
});
