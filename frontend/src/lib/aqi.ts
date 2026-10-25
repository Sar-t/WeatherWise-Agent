/** Air quality colours. India uses CPCB's NAQI categories, in the hues CPCB's own AQI page uses
 * (emerald, lime, yellow, orange, red, dark maroon); elsewhere OpenWeather's 1-5 scale gets a neutral
 * ramp of its own, so it's never mistaken for NAQI. Text colour is chosen for WCAG AA contrast. */

export type AqiStyle = { background: string; text: string; label: string };

export const NAQI_COLOURS: Record<string, string> = {
  Good: "#059669",
  Satisfactory: "#84cc16",
  "Moderately Polluted": "#facc15",
  Poor: "#f97316",
  "Very Poor": "#ef4444",
  Severe: "#7f1d1d",
};
// The API's band name and CPCB's public-page name for 101-200.
NAQI_COLOURS.Moderate = NAQI_COLOURS["Moderately Polluted"];

export const OWM_COLOURS: Record<number, string> = {
  1: "#4d9e8a",
  2: "#9bbf6e",
  3: "#e6c35c",
  4: "#e0894d",
  5: "#b84a45",
};
export const OWM_NAMES: Record<number, string> = { 1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor" };

const INK = "#1c1917";
const WHITE = "#ffffff";

function luminance(hex: string): number {
  const rgb = hex
    .replace("#", "")
    .match(/.{2}/g)!
    .map((h) => parseInt(h, 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Dark or white text, whichever contrasts more with `background`. */
export function readableText(background: string): string {
  return contrastRatio(background, INK) >= contrastRatio(background, WHITE) ? INK : WHITE;
}

export function naqiStyle(category: string | null | undefined): AqiStyle | null {
  if (!category || !(category in NAQI_COLOURS)) return null;
  const background = NAQI_COLOURS[category];
  return { background, text: readableText(background), label: category };
}

export function owmStyle(level: number | null | undefined): AqiStyle | null {
  if (level == null || !(level in OWM_COLOURS)) return null;
  const background = OWM_COLOURS[level];
  return { background, text: readableText(background), label: OWM_NAMES[level] };
}

/** NAQI band edges, for a scale bar: [upper bound, category]. */
export const NAQI_BANDS: [number, string][] = [
  [50, "Good"],
  [100, "Satisfactory"],
  [200, "Moderately Polluted"],
  [300, "Poor"],
  [400, "Very Poor"],
  [500, "Severe"],
];
