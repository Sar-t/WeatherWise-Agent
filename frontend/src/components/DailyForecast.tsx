import type { DayOut } from "@/lib/api";
import { dailySummary, tempColour, weekRange } from "@/lib/details";
import { formatTemp, formatWeekday } from "@/lib/format";
import { Card, CardTitle } from "./Card";
import { ICON_LABELS, WeatherIcon, type IconKey } from "./icons/WeatherIcon";

const DAYS = 7;

/** The week ahead: each day's low-to-high range drawn on one shared scale, so days compare at a glance. */
export function DailyForecast({ daily }: { daily: DayOut[] }) {
  const days = daily.slice(0, DAYS);
  if (!days.length) {
    return (
      <Card>
        <CardTitle>7-day forecast</CardTitle>
        <p className="mt-4 text-[0.95rem] text-ink-soft">The daily forecast isn&apos;t available right now.</p>
      </Card>
    );
  }
  const { min, max } = weekRange(days);
  const range = Math.max(max - min, 1);

  return (
    <Card>
      <CardTitle meta="Low · high">{days.length}-day forecast</CardTitle>
      <p className="sr-only">{dailySummary(days)}</p>
      <ul className="mt-3 divide-y divide-line-soft">
        {days.map((d, i) => {
          const name = i === 0 ? "Today" : formatWeekday(d.date);
          const condition = ICON_LABELS[d.icon as IconKey] ?? d.condition ?? "";
          const rain = Math.round(d.rain_prob_pct ?? 0);
          const left = ((d.min_c - min) / range) * 100;
          const width = Math.max(((d.max_c - d.min_c) / range) * 100, 4);
          return (
            <li
              key={d.date}
              className="grid grid-cols-[3.1rem_1.5rem_2.6rem_2.2rem_minmax(2.5rem,1fr)_2.2rem] items-center gap-2 py-2.5 sm:gap-3"
            >
              <span className="text-[0.95rem] font-medium">{name}</span>
              <WeatherIcon name={d.icon} size={22} className="text-ink-soft" />
              <span className={`tabular text-xs ${rain >= 30 ? "font-semibold text-accent-strong" : "text-muted"}`}>
                <span className="sr-only">{condition}, rain chance </span>
                {rain >= 10 ? `${rain}%` : <span aria-hidden>–</span>}
                {rain < 10 && <span className="sr-only">under 10%</span>}
              </span>
              <span className="tabular text-right text-[0.95rem] text-muted">
                <span className="sr-only">, low </span>
                {formatTemp(d.min_c)}
              </span>
              <span aria-hidden className="relative h-1.5 rounded-full bg-line-soft">
                <span
                  className="absolute inset-y-0 rounded-full"
                  style={{
                    left: `${Math.min(left, 100 - width)}%`,
                    width: `${width}%`,
                    backgroundImage: `linear-gradient(90deg, ${tempColour(d.min_c)}, ${tempColour(d.max_c)})`,
                  }}
                />
              </span>
              <span className="tabular text-[0.95rem] font-medium">
                <span className="sr-only">high </span>
                {formatTemp(d.max_c)}
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
