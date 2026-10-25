/** The hero's sky: a time-of-day phase from local time against sunrise/sunset, softened ("muted")
 * when the sky is grey (cloud, rain, fog, haze, snow). Only the hero changes; the page stays light. */

export type SkyPhase = "dawn" | "day" | "golden" | "dusk" | "night";
export type SkyTheme = { phase: SkyPhase; muted: boolean; dark: boolean };

const GREY_SKIES = new Set(["cloudy", "rain", "heavy-rain", "drizzle", "thunderstorm", "fog", "haze", "snow"]);
const MIN = 60_000;

/**
 * dawn: 45 min before sunrise to 60 min after; golden: 2 h before sunset until 20 min before;
 * dusk: 20 min before sunset to 45 min after; night: outside dawn..dusk; day: the rest.
 * Without sunrise/sunset (polar day/night, missing data) the icon decides day or night.
 */
export function skyTheme(
  icon: string,
  now: string | Date,
  sunrise?: string | null,
  sunset?: string | null,
): SkyTheme {
  const muted = GREY_SKIES.has(icon);
  const t = new Date(now).getTime();
  let phase: SkyPhase;
  if (!sunrise || !sunset) {
    phase = icon.endsWith("night") ? "night" : "day";
  } else {
    const rise = new Date(sunrise).getTime();
    const set = new Date(sunset).getTime();
    if (t < rise - 45 * MIN || t > set + 45 * MIN) phase = "night";
    else if (t < rise + 60 * MIN) phase = "dawn";
    else if (t >= set - 20 * MIN) phase = "dusk";
    else if (t >= set - 120 * MIN) phase = "golden";
    else phase = "day";
  }
  return { phase, muted, dark: phase === "night" };
}

/** CSS gradients per phase; the muted variants pull the colour toward grey. */
export const SKY_GRADIENTS: Record<SkyPhase, { clear: string; muted: string }> = {
  dawn: {
    clear: "linear-gradient(160deg, #f6dfd2 0%, #f3e7dc 38%, #dce8f1 100%)",
    muted: "linear-gradient(160deg, #e7dcd6 0%, #e6e2dd 45%, #d9e0e6 100%)",
  },
  day: {
    clear: "linear-gradient(165deg, #cfe6f5 0%, #e3f0f8 50%, #f4f8fb 100%)",
    muted: "linear-gradient(165deg, #d9e1e8 0%, #e5eaee 55%, #f1f3f5 100%)",
  },
  golden: {
    clear: "linear-gradient(160deg, #f8e3bf 0%, #f6ecd9 45%, #e4eef4 100%)",
    muted: "linear-gradient(160deg, #e9e0cf 0%, #ece7de 50%, #e2e7eb 100%)",
  },
  dusk: {
    clear: "linear-gradient(165deg, #e8c9c4 0%, #d9cfe0 45%, #c8d6e6 100%)",
    muted: "linear-gradient(165deg, #d8cdcc 0%, #d3d2db 50%, #cdd5de 100%)",
  },
  night: {
    clear: "linear-gradient(165deg, #1e3550 0%, #253f5e 55%, #2f4a67 100%)",
    muted: "linear-gradient(165deg, #283646 0%, #2e3d4e 55%, #354556 100%)",
  },
};
