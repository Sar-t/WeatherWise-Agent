"use client";

import { Bar, ComposedChart, Line, XAxis, YAxis } from "recharts";
import type { HourOut } from "@/lib/api";
import { hourlySummary } from "@/lib/details";
import { formatHour, formatTemp } from "@/lib/format";
import { Card, CardTitle } from "./Card";
import { ICON_LABELS, WeatherIcon, type IconKey } from "./icons/WeatherIcon";

const COL = 56; // px per hour; the chart's bands use the same width so points sit under their hour
const CHART_H = 76;

/** The next 24 hours: hour, icon and temperature per column, a temperature line and rain-chance bars. */
export function HourlyStrip({ hours, timeZone }: { hours: HourOut[]; timeZone: string }) {
  if (!hours.length) {
    return (
      <Card>
        <CardTitle>Next 24 hours</CardTitle>
        <p className="mt-4 text-[0.95rem] text-ink-soft">The hourly forecast isn&apos;t available right now.</p>
      </Card>
    );
  }
  const width = hours.length * COL;
  const temps = hours.map((h) => h.temp_c);
  const lo = Math.min(...temps);
  const hi = Math.max(...temps);
  // Keep the line in the upper part of the chart, clear of the rain bars.
  const span = Math.max(hi - lo, 2);
  const data = hours.map((h) => ({ hour: h.time, temp: h.temp_c, rain: h.rain_prob_pct ?? 0 }));
  const summary = hourlySummary(hours, timeZone);
  const grid = { gridTemplateColumns: `repeat(${hours.length}, ${COL}px)` };

  return (
    <Card>
      <CardTitle meta="Temperature · chance of rain">Next 24 hours</CardTitle>
      <figure className="mt-4">
        <div
          tabIndex={0}
          role="group"
          aria-label="Hourly forecast, scrolls sideways"
          className="relative -mx-6 overflow-x-auto px-6 pb-2"
        >
          <div style={{ width }}>
            <ol className="grid text-center" style={grid}>
              {hours.map((h, i) => (
                <li key={h.time} className="flex flex-col items-center gap-1.5">
                  <span className="tabular text-xs text-muted">{i === 0 ? "Now" : `${formatHour(h.time, timeZone)}:00`}</span>
                  <WeatherIcon name={h.icon} size={22} className="text-ink-soft" />
                  <span className="sr-only">{ICON_LABELS[h.icon as IconKey] ?? h.condition}, </span>
                  <span className="tabular text-[0.95rem] font-medium">{formatTemp(h.temp_c)}</span>
                  <span className="sr-only">, rain chance {Math.round(h.rain_prob_pct ?? 0)}%</span>
                </li>
              ))}
            </ol>
            <div aria-hidden className="mt-1">
              <ComposedChart width={width} height={CHART_H} data={data} margin={{ top: 6, right: 0, bottom: 0, left: 0 }}>
                <XAxis dataKey="hour" hide />
                <YAxis yAxisId="temp" hide domain={[lo - span * 0.7, hi + span * 0.05]} />
                <YAxis yAxisId="rain" hide domain={[0, 100]} />
                <Bar yAxisId="rain" dataKey="rain" fill="#b9d3e0" radius={[3, 3, 0, 0]} barSize={20} isAnimationActive={false} />
                <Line
                  yAxisId="temp"
                  dataKey="temp"
                  type="monotone"
                  stroke="var(--color-accent)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
              <div className="grid text-center" style={grid}>
                {hours.map((h) => {
                  const rain = Math.round(h.rain_prob_pct ?? 0);
                  return (
                    <span key={h.time} className={`tabular text-[0.7rem] ${rain >= 30 ? "font-semibold text-accent-strong" : "text-muted"}`}>
                      {rain}%
                    </span>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
        <figcaption className="mt-2 text-xs text-muted">{summary}</figcaption>
      </figure>
    </Card>
  );
}
