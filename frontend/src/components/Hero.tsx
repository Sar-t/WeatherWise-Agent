"use client";

import type { Dashboard } from "@/lib/api";
import { formatTemp, formatTime, secondaryPlaceLine } from "@/lib/format";
import { SKY_GRADIENTS, skyTheme, type SkyPhase } from "@/lib/sky";
import { ICON_LABELS, WeatherIcon, type IconKey } from "./icons/WeatherIcon";

const PHASES: SkyPhase[] = ["dawn", "day", "golden", "dusk", "night"];

/** The sky behind the hero: every phase is a layer; only the active one is visible, so a change
 * fades gently instead of jumping. */
export function SkyBackground({ phase, muted }: { phase: SkyPhase; muted: boolean }) {
  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden>
      {PHASES.flatMap((p) =>
        (["clear", "muted"] as const).map((variant) => (
          <div
            key={`${p}-${variant}`}
            className="sky-layer absolute inset-0"
            data-phase={p}
            data-variant={variant}
            style={{
              backgroundImage: SKY_GRADIENTS[p][variant],
              opacity: p === phase && (variant === "muted") === muted ? 1 : 0,
            }}
          />
        )),
      )}
    </div>
  );
}

export function Hero({ data }: { data: Dashboard }) {
  const { location, current, daily } = data;
  const tz = location.timezone;
  const sky = skyTheme(current.icon, data.local_time, current.sunrise, current.sunset);
  const today = daily[0];
  const condition = current.description
    ? current.description[0].toUpperCase() + current.description.slice(1)
    : ICON_LABELS[current.icon as IconKey] ?? current.condition ?? "";
  const text = sky.dark ? "text-night-ink" : "text-ink";
  const soft = sky.dark ? "text-night-soft" : "text-ink-soft";
  const secondary = secondaryPlaceLine(location.name, location.formatted);

  return (
    <section
      aria-labelledby="hero-place"
      data-sky={sky.phase}
      data-muted={sky.muted}
      className="relative isolate overflow-hidden rounded-[1.75rem] border border-line/70 shadow-card"
    >
      <SkyBackground phase={sky.phase} muted={sky.muted} />
      <div className={`relative grid gap-8 p-6 sm:p-8 lg:grid-cols-[1fr_auto] lg:items-end lg:p-10 ${text}`}>
        <div>
          <h1 id="hero-place" className="font-display text-[1.9rem] leading-tight font-medium tracking-tight sm:text-4xl">
            {location.name}
          </h1>
          {secondary && <p className={`mt-1 text-sm ${soft}`}>{secondary}</p>}

          <div className="mt-6 flex items-start gap-4 sm:mt-8">
            <p className="tabular font-display text-[5.5rem] leading-[0.9] font-light tracking-tight sm:text-[7rem]">
              <span className="sr-only">Temperature </span>
              {formatTemp(current.temp_c)}
              <span className="sr-only">C</span>
            </p>
            <WeatherIcon
              name={current.icon}
              size={64}
              strokeWidth={1.25}
              animate
              className={`mt-2 sm:mt-3 sm:h-20 sm:w-20 ${sky.dark ? "text-night-ink" : "text-accent"}`}
              title={ICON_LABELS[current.icon as IconKey]}
            />
          </div>

          <p className="mt-4 text-lg font-medium">{condition}</p>
          <p className={`tabular mt-1 text-[0.95rem] ${soft}`}>
            Feels like {formatTemp(current.feels_like_c)}
            {today && (
              <>
                <span aria-hidden className="mx-2">·</span>
                <span className="sr-only">Today&apos;s high </span>H {formatTemp(today.max_c)}
                <span aria-hidden className="mx-1.5" />
                <span className="sr-only">and low </span>L {formatTemp(today.min_c)}
              </>
            )}
          </p>
        </div>

        <p className={`tabular text-sm ${soft}`}>Observed {formatTime(current.time, tz)}</p>
      </div>
    </section>
  );
}
