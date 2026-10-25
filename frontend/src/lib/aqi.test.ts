import { describe, expect, it } from "vitest";
import { NAQI_COLOURS, OWM_COLOURS, contrastRatio, naqiStyle, owmStyle, readableText } from "./aqi";

const CATEGORIES = ["Good", "Satisfactory", "Moderately Polluted", "Poor", "Very Poor", "Severe"];

describe("AQI colours", () => {
  it("gives every NAQI and OpenWeather chip text with at least AA contrast", () => {
    for (const bg of [...Object.values(NAQI_COLOURS), ...Object.values(OWM_COLOURS)]) {
      expect(contrastRatio(bg, readableText(bg))).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("uses white text on Severe's dark maroon, clearly darker than Very Poor", () => {
    expect(naqiStyle("Severe")?.text).toBe("#ffffff");
    expect(contrastRatio(NAQI_COLOURS.Severe, "#ffffff")).toBeGreaterThan(contrastRatio(NAQI_COLOURS["Very Poor"], "#ffffff") * 2);
  });

  it("has a distinct colour per NAQI category, and Moderate as an alias", () => {
    expect(new Set(CATEGORIES.map((c) => NAQI_COLOURS[c])).size).toBe(6);
    expect(naqiStyle("Moderate")?.background).toBe(NAQI_COLOURS["Moderately Polluted"]);
  });

  it("labels the OpenWeather 1-5 scale with its own names and rejects unknowns", () => {
    expect(owmStyle(2)?.label).toBe("Fair");
    expect(owmStyle(9)).toBeNull();
    expect(naqiStyle("Unknown")).toBeNull();
  });
});
