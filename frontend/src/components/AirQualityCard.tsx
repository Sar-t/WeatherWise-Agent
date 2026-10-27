"use client";

import dynamic from "next/dynamic";
import type { DashboardAir } from "@/lib/api";
import { NAQI_COLOURS, OWM_COLOURS, naqiStyle, owmStyle } from "@/lib/aqi";
import { formatHour, formatTime } from "@/lib/format";
import { Card, CardTitle } from "./Card";
import { WhenVisible } from "./WhenVisible";

// Recharts is the largest library on the page: load it once the card is near the viewport.
const AirChart = dynamic(() => import("./charts/AirChart"), { ssr: false, loading: () => <div className="h-full" /> });

const POLLUTANT_NAMES: Record<string, string> = {
  "PM2.5": "PM2.5",
  PM10: "PM10",
  O3: "Ozone (O₃)",
  NO2: "Nitrogen dioxide (NO₂)",
  SO2: "Sulphur dioxide (SO₂)",
  CO: "Carbon monoxide (CO)",
  NH3: "Ammonia (NH₃)",
};

export function airSummary(air: DashboardAir, timeZone: string): string {
  const values = (air.hourly ?? []).filter((h) => h.value != null);
  if (!values.length) return "No forecast for the next 24 hours.";
  const worst = values.reduce((a, b) => ((b.value ?? 0) > (a.value ?? 0) ? b : a));
  const best = values.reduce((a, b) => ((b.value ?? 0) < (a.value ?? 0) ? b : a));
  const unit = air.scale === "naqi" ? "NAQI" : "level";
  return (
    `Next 24 hours: ${unit} ${best.value}${air.scale === "owm" ? "/5" : ""} to ${worst.value}${air.scale === "owm" ? "/5" : ""}; ` +
    `worst around ${formatTime(worst.time, timeZone)} (${worst.category}).`
  );
}

export function AirQualityCard({ air, timeZone }: { air: DashboardAir | null | undefined; timeZone: string }) {
  if (!air) {
    return (
      <Card>
        <Title scale={null} />
        <p className="mt-4 text-[0.95rem] text-ink-soft">Air quality isn&apos;t available for this place right now.</p>
      </Card>
    );
  }
  const style = air.scale === "naqi" ? naqiStyle(air.category) : owmStyle(air.value);
  const chart = (air.hourly ?? []).map((h) => ({
    hour: formatHour(h.time, timeZone),
    time: formatTime(h.time, timeZone),
    value: h.value ?? 0,
    category: h.category ?? "",
    colour: air.scale === "naqi" ? NAQI_COLOURS[h.category ?? ""] ?? "#d6d3cd" : OWM_COLOURS[h.value ?? 0] ?? "#d6d3cd",
  }));
  const summary = airSummary(air, timeZone);

  return (
    <Card>
      <Title scale={air.scale} />
      <div className="mt-4 flex flex-wrap items-end gap-x-4 gap-y-2">
        <p className="tabular font-display text-[3.4rem] leading-none font-light tracking-tight">
          <span className="sr-only">{air.scale === "naqi" ? "NAQI " : "Level "}</span>
          {air.value ?? "–"}
          {air.scale === "owm" && <span className="text-2xl text-muted">/5</span>}
        </p>
        {style && (
          <span
            className="mb-1.5 inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold"
            style={{ background: style.background, color: style.text }}
          >
            {style.label}
          </span>
        )}
      </div>

      <dl className="mt-5 grid grid-cols-3 gap-3 text-sm">
        {air.scale === "naqi" && (
          <Stat label="Main pollutant" value={air.dominant_pollutant ? POLLUTANT_NAMES[air.dominant_pollutant] ?? air.dominant_pollutant : "–"} />
        )}
        <Stat label="PM2.5" value={air.pm2_5 != null ? `${air.pm2_5}` : "–"} unit="µg/m³" />
        <Stat label="PM10" value={air.pm10 != null ? `${air.pm10}` : "–"} unit="µg/m³" />
      </dl>

      {chart.length > 0 && (
        <figure className="mt-6">
          <figcaption className="mb-2 flex items-baseline justify-between text-xs text-muted">
            <span>Next 24 hours</span>
            <span className="sr-only">{summary}</span>
          </figcaption>
          <WhenVisible className="h-20" aria-hidden>
            <AirChart data={chart} scale={air.scale} />
          </WhenVisible>
          <p className="mt-2 text-xs text-muted" aria-hidden>{summary}</p>
        </figure>
      )}
    </Card>
  );
}

function Title({ scale }: { scale: "naqi" | "owm" | null }) {
  return (
    <CardTitle meta={scale && (scale === "naqi" ? "India NAQI (CPCB)" : "OpenWeather scale, 1–5")}>Air quality</CardTitle>
  );
}

function Stat({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="tabular mt-0.5 whitespace-nowrap font-medium text-ink">
        {value}
        {unit && <span className="ml-1 text-xs font-normal text-muted">{unit}</span>}
      </dd>
    </div>
  );
}
