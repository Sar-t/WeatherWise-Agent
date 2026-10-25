import { describe, expect, it } from "vitest";
import delhi from "@/test/fixtures/dashboard-delhi.json";
import type { Dashboard } from "./api";
import {
  dayLength,
  hourlySummary,
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
