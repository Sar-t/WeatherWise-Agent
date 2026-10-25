import { describe, expect, it } from "vitest";
import delhi from "@/test/fixtures/dashboard-delhi.json";
import type { Dashboard } from "./api";
import { rainWhen, suggestions } from "./suggestions";

const TZ = "Asia/Kolkata";
const NOW = "2026-10-25T13:06:00+05:30";
const at = (day: string, hhmm: string) => `2026-10-${day}T${hhmm}:00+05:30`;

describe("rainWhen", () => {
  it.each([
    [at("25", "13:30"), "this afternoon"],
    [at("25", "16:30"), "this afternoon"],
    [at("25", "17:00"), "this evening"],
    [at("25", "20:30"), "this evening"],
    [at("25", "21:00"), "tonight"],
    [at("26", "02:30"), "tonight"],
    [at("26", "06:00"), "tomorrow"],
    [at("26", "11:30"), "tomorrow"],
  ])("rain at %s is %s", (time, words) => {
    expect(rainWhen(time, NOW, TZ)).toBe(words);
  });

  it("calls the rest of the morning this morning", () => {
    expect(rainWhen(at("25", "09:30"), at("25", "07:10"), TZ)).toBe("this morning");
  });
});

describe("suggestions", () => {
  // The real Delhi response: no rain, NAQI Satisfactory.
  const base = delhi as Dashboard;

  it("falls back to the two general questions on a calm day", () => {
    expect(suggestions(base)).toEqual(["Best time for a run tomorrow?", "What should I wear today?"]);
  });

  it("asks about rain from the first hour with at least 40%", () => {
    const hourly = base.hourly.map((h, i) => ({ ...h, rain_prob_pct: i === 6 ? 60 : i === 9 ? 80 : 0 }));
    const data = { ...base, hourly };
    const expected = `Will it rain ${rainWhen(hourly[6].time, base.local_time, TZ)}?`;
    expect(suggestions(data)).toEqual([expected, "Best time for a run tomorrow?", "What should I wear today?"]);
  });

  it("puts a walk-safety question first when NAQI is Poor or worse, and stops at 3", () => {
    const data = {
      ...base,
      air_quality: { ...base.air_quality!, value: 245, category: "Poor" },
      hourly: base.hourly.map((h) => ({ ...h, rain_prob_pct: 50 })),
    };
    const chips = suggestions(data);
    expect(chips).toHaveLength(3);
    expect(chips[0]).toBe("Is it safe to go for a walk today?");
    expect(chips[1]).toMatch(/^Will it rain /);
  });
});
