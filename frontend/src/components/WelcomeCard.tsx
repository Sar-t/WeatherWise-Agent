"use client";

import { SkyBackground } from "./Hero";
import { UiIcon } from "./icons/UiIcon";

/** First visit: no saved location and no ?place=. Nothing is requested until the user acts. */
export function WelcomeCard({ onUseLocation, onSearch, locating }: {
  onUseLocation: () => void;
  onSearch: () => void;
  locating: boolean;
}) {
  return (
    <section
      aria-labelledby="welcome-title"
      className="relative isolate overflow-hidden rounded-[1.75rem] border border-line/70 shadow-card"
    >
      <SkyBackground phase="day" muted={false} />
      <div className="relative max-w-xl p-8 sm:p-12">
        <p className="text-sm font-medium tracking-wide text-accent-strong uppercase">Welcome</p>
        <h1 id="welcome-title" className="mt-3 font-display text-[2rem] leading-tight font-medium tracking-tight sm:text-[2.6rem]">
          Weather, air quality and the best time to step out.
        </h1>
        <p className="mt-4 text-[1.02rem] leading-relaxed text-ink-soft">
          Share your location for a forecast right where you are, or search for any city. We only use your
          location to look up the weather, rounded to about a kilometre.
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={onUseLocation}
            disabled={locating}
            className="inline-flex h-12 items-center gap-2 rounded-full bg-accent px-6 text-[0.95rem] font-semibold text-white shadow-raised transition hover:bg-accent-strong disabled:opacity-70"
          >
            <UiIcon name="locate" />
            {locating ? "Locating…" : "Use my location"}
          </button>
          <button
            type="button"
            onClick={onSearch}
            className="inline-flex h-12 items-center gap-2 rounded-full border border-line bg-surface px-6 text-[0.95rem] font-medium text-ink transition hover:border-accent"
          >
            <UiIcon name="search" className="text-muted" />
            Search for a city
          </button>
        </div>
      </div>
    </section>
  );
}
