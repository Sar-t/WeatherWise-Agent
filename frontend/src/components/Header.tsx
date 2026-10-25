"use client";

import Link from "next/link";
import { useEffect, useId, useState, type FormEvent, type RefObject } from "react";
import type { Candidate } from "@/lib/api";
import { localClock } from "@/lib/format";
import { UiIcon } from "./icons/UiIcon";

type Props = {
  timeZone?: string;
  placeName?: string;
  searchRef: RefObject<HTMLInputElement | null>;
  onSearch: (name: string) => void;
  onUseLocation: () => void;
  locating: boolean;
  searching: boolean;
  candidates: Candidate[];
  onPickCandidate: (c: Candidate) => void;
  onDismissCandidates: () => void;
  message: string | null; // e.g. "No place found", "Location permission denied"
};

function Clock({ timeZone, placeName }: { timeZone: string; placeName?: string }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <p className="tabular hidden text-sm text-muted sm:block" aria-live="off">
      <span className="sr-only">Local time{placeName ? ` in ${placeName}` : ""}: </span>
      {localClock(timeZone, now)}
      <span className="ml-1 text-xs">{shortZone(timeZone, now)}</span>
    </p>
  );
}

function shortZone(timeZone: string, now: Date): string {
  const part = new Intl.DateTimeFormat("en-IN", { timeZone, timeZoneName: "short" })
    .formatToParts(now)
    .find((p) => p.type === "timeZoneName");
  return part?.value ?? "";
}

export function Header(props: Props) {
  const { timeZone, placeName, searchRef, onSearch, onUseLocation, locating, searching, candidates } = props;
  const [query, setQuery] = useState("");
  const listId = useId();
  const messageId = useId();

  function submit(event: FormEvent) {
    event.preventDefault();
    if (query.trim()) onSearch(query.trim());
  }

  return (
    <header className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-4 pt-5 sm:px-6 lg:px-8">
      <Link href="/" className="flex items-center gap-2 rounded-lg text-ink" aria-label="WeatherWise home">
        <Mark />
        <span className="font-display text-[1.35rem] font-semibold tracking-tight">WeatherWise</span>
      </Link>

      <div className="relative order-last w-full sm:order-none sm:w-auto sm:flex-1 sm:max-w-md">
        <form role="search" onSubmit={submit} className="flex items-center gap-2">
          <label htmlFor="city-search" className="sr-only">
            Search for a city
          </label>
          <div className="flex h-11 flex-1 items-center gap-2 rounded-full border border-line bg-surface px-4 shadow-card transition focus-within:border-accent">
            <UiIcon name="search" className="shrink-0 text-muted" />
            <input
              ref={searchRef}
              id="city-search"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search a city, e.g. Pune"
              autoComplete="off"
              maxLength={100}
              aria-describedby={props.message ? messageId : undefined}
              aria-controls={candidates.length ? listId : undefined}
              className="h-full w-full bg-transparent text-[0.95rem] text-ink placeholder:text-muted focus:outline-none"
            />
            {searching && <span className="text-xs text-muted">Searching…</span>}
          </div>
        </form>

        {props.message && (
          <p id={messageId} role="status" className="mt-2 px-4 text-sm text-ink-soft">
            {props.message}
          </p>
        )}

        {candidates.length > 0 && (
          <div
            className="absolute inset-x-0 top-full z-20 mt-2 rounded-2xl border border-line bg-surface p-2 shadow-raised"
            role="dialog"
            aria-label="Did you mean"
          >
            <div className="flex items-center justify-between px-3 pb-1 pt-1">
              <p className="text-sm font-semibold text-ink">Did you mean</p>
              <button
                type="button"
                onClick={props.onDismissCandidates}
                className="rounded-full p-1 text-muted hover:text-ink"
                aria-label="Close suggestions"
              >
                <UiIcon name="close" size={16} />
              </button>
            </div>
            <ul id={listId} className="flex flex-col">
              {candidates.map((c) => (
                <li key={`${c.lat},${c.lon}`}>
                  <button
                    type="button"
                    onClick={() => props.onPickCandidate(c)}
                    className="flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left hover:bg-canvas"
                  >
                    <UiIcon name="pin" className="mt-0.5 shrink-0 text-accent" />
                    <span>
                      <span className="block text-[0.95rem] font-medium text-ink">{c.name}</span>
                      <span className="block text-sm text-muted">
                        {[c.state, c.country].filter(Boolean).join(", ")}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="ml-auto flex items-center gap-4">
        {timeZone && <Clock timeZone={timeZone} placeName={placeName} />}
        <button
          type="button"
          onClick={onUseLocation}
          disabled={locating}
          aria-label={locating ? "Locating" : "Use my location"}
          className="inline-flex h-11 items-center gap-2 rounded-full border border-line bg-surface px-4 text-sm font-medium text-ink shadow-card transition hover:border-accent hover:text-accent-strong disabled:opacity-60"
        >
          <UiIcon name="locate" className="text-accent" />
          <span className="hidden min-[400px]:inline">{locating ? "Locating…" : "Use my location"}</span>
        </button>
      </div>
    </header>
  );
}

function Mark() {
  // A sun half-risen over a horizon: the wordmark's small companion.
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
         strokeLinecap="round" aria-hidden className="text-accent">
      <path d="M6.5 15.5a5.5 5.5 0 0 1 11 0" />
      <line x1="3" y1="15.5" x2="21" y2="15.5" />
      <line x1="7" y1="19" x2="17" y2="19" />
      <line x1="12" y1="4.5" x2="12" y2="6.5" />
      <line x1="5.2" y1="7.8" x2="6.6" y2="9.2" />
      <line x1="18.8" y1="7.8" x2="17.4" y2="9.2" />
    </svg>
  );
}
