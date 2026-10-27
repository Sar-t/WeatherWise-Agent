"use client";

import { useEffect, useRef, useState } from "react";
import type { HourOut } from "@/lib/api";
import { hourlySummary } from "@/lib/details";
import { formatHour, formatTemp } from "@/lib/format";
import { Card, CardTitle } from "./Card";
import HourlyChart from "./charts/HourlyChart";
import { UiIcon } from "./icons/UiIcon";
import { ICON_LABELS, WeatherIcon, type IconKey } from "./icons/WeatherIcon";

const COL = 56; // px per hour; the chart's bands use the same width so points sit under their hour
const CHART_H = 56; // with rain bars under the line
const LINE_H = 36; // line only, when no hour has any chance of rain
const STEP = COL * 4;


/** The next 24 hours: hour, icon and temperature per column, a temperature line and rain-chance bars. */
export function HourlyStrip({ hours, timeZone }: { hours: HourOut[]; timeZone: string }) {
  const scroller = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: true, end: false });

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const update = () =>
      setEdges({ start: el.scrollLeft <= 1, end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 1 });
    const observer = new ResizeObserver(update);
    observer.observe(el);
    el.addEventListener("scroll", update, { passive: true });
    return () => {
      observer.disconnect();
      el.removeEventListener("scroll", update);
    };
  }, []);

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
  const span = Math.max(hi - lo, 2);
  const anyRain = hours.some((h) => Math.round(h.rain_prob_pct ?? 0) > 0);
  // With rain bars, keep the line in the upper part of the chart, clear of them.
  const domain = anyRain ? [lo - span * 0.7, hi + span * 0.05] : [lo - span * 0.1, hi + span * 0.1];
  const data = hours.map((h) => ({ hour: h.time, temp: h.temp_c, rain: h.rain_prob_pct ?? 0 }));
  const summary = hourlySummary(hours, timeZone);
  const grid = { gridTemplateColumns: `repeat(${hours.length}, ${COL}px)` };
  const scrollBy = (dx: number) => scroller.current?.scrollBy({ left: dx, behavior: "smooth" });

  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <CardTitle>Next 24 hours</CardTitle>
        <div className="flex items-center gap-3">
          <p className="hidden text-xs text-muted min-[480px]:block">
            {anyRain ? "Temperature · chance of rain" : "Temperature"}
          </p>
          <div className="hidden gap-1 sm:flex">
            <ScrollButton dir="left" label="Earlier hours" disabled={edges.start} onClick={() => scrollBy(-STEP)} />
            <ScrollButton dir="right" label="Later hours" disabled={edges.end} onClick={() => scrollBy(STEP)} />
          </div>
        </div>
      </div>

      <figure className="mt-4">
        <div className="relative">
          <div
            ref={scroller}
            tabIndex={0}
            role="group"
            aria-label="Hourly forecast, scrolls sideways"
            className="relative -mx-6 snap-x snap-mandatory scroll-px-6 overflow-x-auto px-6 pb-2 scrollbar-thin"
          >
            <div style={{ width }}>
              <ol className="grid text-center" style={grid}>
                {hours.map((h, i) => (
                  <li key={h.time} className="flex snap-start flex-col items-center gap-1.5">
                    <span className="tabular text-xs text-muted">{i === 0 ? "Now" : `${formatHour(h.time, timeZone)}:00`}</span>
                    <WeatherIcon name={h.icon} size={22} className="text-ink-soft" />
                    <span className="sr-only">{ICON_LABELS[h.icon as IconKey] ?? h.condition}, </span>
                    <span className="tabular text-[0.95rem] font-medium">{formatTemp(h.temp_c)}</span>
                    <span className="sr-only">, rain chance {Math.round(h.rain_prob_pct ?? 0)}%</span>
                  </li>
                ))}
              </ol>
              <div aria-hidden className="mt-1">
                <HourlyChart width={width} height={anyRain ? CHART_H : LINE_H} data={data} domain={domain} rain={anyRain} />
                {anyRain && (
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
                )}
              </div>
            </div>
          </div>
          {/* Soft fades where more hours are hidden, so the edge never looks like a cut-off label. */}
          <div
            aria-hidden
            className={`pointer-events-none absolute inset-y-0 -left-6 w-12 bg-linear-to-r from-surface to-transparent transition-opacity ${edges.start ? "opacity-0" : "opacity-100"}`}
          />
          <div
            aria-hidden
            className={`pointer-events-none absolute inset-y-0 -right-6 w-16 bg-linear-to-l from-surface via-surface/80 to-transparent transition-opacity ${edges.end ? "opacity-0" : "opacity-100"}`}
          />
        </div>
        {!anyRain && <p className="text-xs text-muted">No rain expected</p>}
        <figcaption className="mt-2 text-xs text-muted">{summary}</figcaption>
      </figure>
    </Card>
  );
}

function ScrollButton({ dir, label, disabled, onClick }: { dir: "left" | "right"; label: string; disabled: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="grid h-8 w-8 place-items-center rounded-full border border-line bg-surface text-ink-soft transition hover:border-accent hover:text-accent-strong disabled:opacity-35 disabled:hover:border-line disabled:hover:text-ink-soft"
    >
      <UiIcon name={dir === "left" ? "chevron-left" : "chevron-right"} size={16} />
    </button>
  );
}
