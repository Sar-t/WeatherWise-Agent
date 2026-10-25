import { describe, expect, it } from "vitest";
import { skyTheme } from "./sky";

// Delhi, 25 Oct 2026 (from the API): sunrise 06:27, sunset 17:42 IST.
const RISE = "2026-10-25T06:27:52+05:30";
const SET = "2026-10-25T17:42:25+05:30";
const at = (hhmm: string) => `2026-10-25T${hhmm}:00+05:30`;

describe("skyTheme", () => {
  it.each([
    ["05:00", "night"],
    ["06:00", "dawn"],
    ["07:20", "dawn"],
    ["12:00", "day"],
    ["16:00", "golden"],
    ["17:30", "dusk"],
    ["18:20", "dusk"],
    ["19:00", "night"],
  ])("%s is %s", (time, phase) => {
    expect(skyTheme("clear-day", at(time), RISE, SET).phase).toBe(phase);
  });

  it("only goes dark at night", () => {
    expect(skyTheme("clear-night", at("23:00"), RISE, SET).dark).toBe(true);
    expect(skyTheme("clear-day", at("17:30"), RISE, SET).dark).toBe(false);
  });

  it("mutes grey skies and not clear ones", () => {
    expect(skyTheme("rain", at("12:00"), RISE, SET).muted).toBe(true);
    expect(skyTheme("haze", at("12:00"), RISE, SET).muted).toBe(true);
    expect(skyTheme("partly-cloudy", at("12:00"), RISE, SET).muted).toBe(false);
  });

  it("falls back to the icon without sun times", () => {
    expect(skyTheme("clear-night", at("12:00")).phase).toBe("night");
    expect(skyTheme("cloudy", at("23:00"), null, null).phase).toBe("day");
  });
});
