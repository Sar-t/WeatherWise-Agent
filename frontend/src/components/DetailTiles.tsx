import type { ReactNode } from "react";
import type { CurrentOut } from "@/lib/api";
import {
  dayLength,
  humidityLabel,
  sunProgress,
  uvLabel,
  visibilityLabel,
  windArrowRotation,
} from "@/lib/details";
import { formatTime } from "@/lib/format";
import { Card, CardTitle } from "./Card";

/** Current conditions in detail: wind, humidity, UV, visibility, pressure, and the sun's day. */
export function DetailTiles({ current, now, timeZone }: { current: CurrentOut; now: string; timeZone: string }) {
  const uv = current.uv_index;
  const vis = current.visibility_km;
  return (
    <Card>
      <CardTitle>Right now</CardTitle>
      <dl className="mt-4 grid grid-cols-2 gap-3">
        <Tile label="Wind" note={windNote(current)}>
          <span className="flex items-center gap-2">
            {current.wind_deg != null && <WindArrow fromDeg={current.wind_deg} />}
            {Math.round(current.wind_kmh)}
            <Unit>km/h</Unit>
          </span>
        </Tile>
        <Tile label="Humidity" note={humidityLabel(current.humidity_pct)}>
          {Math.round(current.humidity_pct)}
          <Unit>%</Unit>
        </Tile>
        <Tile label="UV index" note={uv != null ? uvLabel(uv) : "Not available"}>
          {uv != null ? Math.round(uv) : "–"}
        </Tile>
        <Tile label="Visibility" note={vis != null ? visibilityLabel(vis) : "Not available"}>
          {vis != null ? (vis >= 10 ? "10+" : vis.toFixed(vis < 1 ? 1 : 0)) : "–"}
          {vis != null && <Unit>km</Unit>}
        </Tile>
        <Tile label="Pressure" note="Sea level">
          {current.pressure_hpa != null ? Math.round(current.pressure_hpa) : "–"}
          {current.pressure_hpa != null && <Unit>hPa</Unit>}
        </Tile>
        <Tile label="Feels like" note={feelsNote(current)}>
          {Math.round(current.feels_like_c)}
          <Unit>°C</Unit>
        </Tile>
        {current.sunrise && current.sunset && (
          <div className="col-span-2 rounded-2xl bg-canvas px-4 py-3.5">
            <dt className="text-xs text-muted">Sunrise and sunset</dt>
            <dd className="mt-1">
              <SunArc now={now} sunrise={current.sunrise} sunset={current.sunset} timeZone={timeZone} />
            </dd>
          </div>
        )}
      </dl>
    </Card>
  );
}

function windNote(c: CurrentOut): string {
  const parts = [];
  if (c.wind_dir) parts.push(`From ${c.wind_dir}`);
  if (c.gust_kmh != null && c.gust_kmh > c.wind_kmh) parts.push(`gusts ${Math.round(c.gust_kmh)} km/h`);
  return parts.join(", ") || "Calm";
}

function feelsNote(c: CurrentOut): string {
  const diff = Math.round(c.feels_like_c - c.temp_c);
  if (diff >= 2) return "Warmer than it is";
  if (diff <= -2) return "Cooler than it is";
  return "About the same";
}

function Tile({ label, note, children }: { label: string; note: string; children: ReactNode }) {
  return (
    <div className="rounded-2xl bg-canvas px-4 py-3.5">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="tabular mt-1 text-[1.35rem] leading-tight font-medium text-ink">{children}</dd>
      <dd className="mt-0.5 text-xs text-ink-soft">{note}</dd>
    </div>
  );
}

function Unit({ children }: { children: ReactNode }) {
  return <span className="ml-1 text-sm font-normal text-muted">{children}</span>;
}

function WindArrow({ fromDeg }: { fromDeg: number }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden className="text-accent"
         style={{ transform: `rotate(${windArrowRotation(fromDeg)}deg)` }}>
      <line x1="12" y1="20" x2="12" y2="4" />
      <polyline points="6.5 9.5 12 4 17.5 9.5" />
    </svg>
  );
}

const R = 80;
const CX = 100;
const BASE = 92;

/** The sun's path over the day, with where it is now. */
export function SunArc({ now, sunrise, sunset, timeZone }: { now: string; sunrise: string; sunset: string; timeZone: string }) {
  const p = sunProgress(now, sunrise, sunset);
  const track = `M ${CX - R} ${BASE} A ${R} ${R} 0 0 1 ${CX + R} ${BASE}`;
  const angle = p != null ? Math.PI * (1 - p) : null;
  const sun = angle != null ? { x: CX + R * Math.cos(angle), y: BASE - R * Math.sin(angle) } : null;
  const rise = formatTime(sunrise, timeZone);
  const set = formatTime(sunset, timeZone);
  const label =
    p == null
      ? `Sunrise ${rise}, sunset ${set}. The sun is down.`
      : `Sunrise ${rise}, sunset ${set}. ${Math.round(p * 100)}% of daylight has passed.`;

  return (
    <figure>
      <svg viewBox="0 0 200 100" className="mx-auto block w-full max-w-[16rem]" role="img" aria-label={label}>
        <line x1="8" y1={BASE} x2="192" y2={BASE} stroke="var(--color-line)" strokeWidth="1" />
        <path d={track} fill="none" stroke="var(--color-line)" strokeWidth="1.5" strokeDasharray="3 4" />
        {sun && angle != null && (
          <>
            <path
              d={`M ${CX - R} ${BASE} A ${R} ${R} 0 0 1 ${sun.x.toFixed(2)} ${sun.y.toFixed(2)}`}
              fill="none"
              stroke="#e0a64f"
              strokeWidth="2"
              strokeLinecap="round"
            />
            <circle cx={sun.x} cy={sun.y} r="6" fill="#f2b84b" stroke="var(--color-surface)" strokeWidth="2" />
          </>
        )}
      </svg>
      <figcaption className="tabular mt-1 flex justify-between text-sm">
        <span>
          <span className="block text-xs text-muted">Sunrise</span>
          {rise}
        </span>
        <span className="self-end text-xs text-muted">{dayLength(sunrise, sunset)} of daylight</span>
        <span className="text-right">
          <span className="block text-xs text-muted">Sunset</span>
          {set}
        </span>
      </figcaption>
    </figure>
  );
}
