import { describe, expect, it } from "vitest";
import {
  formatHour,
  formatPercent,
  formatTemp,
  formatTime,
  formatWeekday,
  keepUnits,
  secondaryPlaceLine,
} from "./format";

describe("times are shown in the place's own timezone", () => {
  const instant = "2026-10-25T07:30:00Z";

  it("formats the same instant per timezone, 24-hour", () => {
    expect(formatTime(instant, "Asia/Kolkata")).toBe("13:00");
    expect(formatTime(instant, "America/Los_Angeles")).toBe("00:30");
    expect(formatTime("2026-10-25T13:05:00+05:30", "Asia/Kolkata")).toBe("13:05");
  });

  it("pads the hour", () => {
    expect(formatHour("2026-10-25T03:00:00+05:30", "Asia/Kolkata")).toBe("03");
  });

  it("reads a calendar date as that day, whatever the zone", () => {
    // 25 Oct 2026 is a Sunday; the old noon-UTC reading gave Mon in UTC+14 (Kiritimati).
    expect(formatWeekday("2026-10-25")).toBe("Sun");
  });
});

describe("rounding", () => {
  it("rounds temperatures to whole degrees with a real minus sign", () => {
    expect(formatTemp(30.4)).toBe("30°");
    expect(formatTemp(30.5)).toBe("31°");
    expect(formatTemp(-3.2)).toBe("−3°");
    expect(formatTemp(-0.2)).toBe("0°");
  });

  it("rounds percentages and shows a dash when missing", () => {
    expect(formatPercent(41.6)).toBe("42%");
    expect(formatPercent(null)).toBe("–");
  });
});

describe("secondaryPlaceLine", () => {
  it("drops what the name already says and short state codes", () => {
    expect(secondaryPlaceLine("New Delhi", "New Delhi, India")).toBe("India");
    expect(secondaryPlaceLine("Paris", "Paris, IDF, France")).toBe("France");
    expect(secondaryPlaceLine("Los Angeles, United States", "Los Angeles, CA, United States of America")).toBe("");
  });
});

describe("keepUnits", () => {
  const NBSP = String.fromCharCode(160);

  it("joins a number to its unit with a non-breaking space", () => {
    expect(keepUnits("From W, gusts 48 km/h")).toBe(`From W, gusts 48${NBSP}km/h`);
    expect(keepUnits("Wind 21 km/h and 2 mm of rain")).toBe(`Wind 21${NBSP}km/h and 2${NBSP}mm of rain`);
  });

  it("leaves words that only start like a unit alone", () => {
    expect(keepUnits("2 hours left")).toBe("2 hours left");
  });
});
