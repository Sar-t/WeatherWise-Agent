"use client";

import { useCallback, useRef, useState, useSyncExternalStore } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, fetchDashboard, placeKey, type Candidate, type Place } from "@/lib/api";
import { getQueryClient } from "@/app/providers";
import { loadPlace, savePlace } from "@/lib/location-store";
import type { Dashboard as DashboardData } from "@/lib/api";
import { formatTime } from "@/lib/format";
import { AirQualityCard } from "./AirQualityCard";
import { AssistantButton } from "./assistant/AssistantButton";
import { AlertsBanner } from "./AlertsBanner";
import { BestTimeCard } from "./BestTimeCard";
import { DailyForecast } from "./DailyForecast";
import { Footer } from "./Footer";
import { WhenVisible } from "./WhenVisible";
import { DetailTiles, SunCard } from "./DetailTiles";
import { HourlyStrip } from "./HourlyStrip";
import { Header } from "./Header";
import { Hero } from "./Hero";
import { WelcomeCard } from "./WelcomeCard";
import { DashboardSkeleton, ErrorCard } from "./States";

// The assistant panel (and its chat code) loads on first open, so the dashboard paints without it.
const AssistantPanel = dynamic(() => import("./assistant/Assistant").then((m) => m.Assistant), { ssr: false });

export function dashboardQuery(place: Place) {
  return {
    queryKey: ["dashboard", placeKey(place)],
    queryFn: async ({ signal }: { signal: AbortSignal }) => {
      const data = await fetchDashboard(place, signal);
      savePlace(place); // remember what loaded, for the next visit
      return data;
    },
  };
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return "Too many requests, try again in a moment.";
    if (error.status === 404) return "We couldn't find that place. Try another spelling or a nearby city.";
    return error.message;
  }
  return "Something went wrong. Please try again.";
}

const noop = () => () => {};

/** The first place to show: ?place=, else the remembered one, else none (a first visit). */
function initialPlace(params: URLSearchParams | null): Place | null {
  const fromUrl = params?.get("place")?.trim();
  return fromUrl ? { kind: "place", name: fromUrl } : loadPlace();
}

// Start the first dashboard query as soon as this code loads, not after hydration: on a slow phone
// that's seconds sooner. It takes over the request the page head already started (early-fetch.ts),
// and has the component's query key, so the component picks it up instead of asking again.
if (typeof window !== "undefined") {
  const first = initialPlace(new URLSearchParams(window.location.search));
  if (first) void getQueryClient().prefetchQuery(dashboardQuery(first));
}

export function Dashboard() {
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const searchRef = useRef<HTMLInputElement>(null);
  // Storage only exists in the browser: read it after mount, never during server rendering.
  const mounted = useSyncExternalStore(noop, () => true, () => false);
  const [chosen, setChosen] = useState<Place | null | undefined>(undefined); // undefined: not chosen yet
  const place = chosen !== undefined ? chosen : mounted ? initialPlace(params) : undefined;

  const [locating, setLocating] = useState(false);
  const [searching, setSearching] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantLoaded, setAssistantLoaded] = useState(false); // the panel's code loads on first open
  const assistantButton = useRef<HTMLButtonElement>(null);

  const shown = useQuery({
    ...(place ? dashboardQuery(place) : { queryKey: ["dashboard", null], queryFn: () => Promise.reject() }),
    enabled: !!place,
  });

  /** Load a new place before showing it: the current dashboard stays until it arrives. */
  const tryPlace = useCallback(
    async (next: Place) => {
      setNote(null);
      setCandidates([]);
      setSearching(true);
      try {
        await queryClient.fetchQuery(dashboardQuery(next));
        setChosen(next);
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) setCandidates(error.candidates);
        else setNote(errorMessage(error));
      } finally {
        setSearching(false);
      }
    },
    [queryClient],
  );

  const useLocation = useCallback(() => {
    setNote(null);
    if (!("geolocation" in navigator)) {
      setNote("Location isn't available in this browser. Search for a city instead.");
      searchRef.current?.focus();
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        // Rounded right away: about a kilometre is plenty for weather.
        void tryPlace({
          kind: "coords",
          lat: Math.round(pos.coords.latitude * 100) / 100,
          lon: Math.round(pos.coords.longitude * 100) / 100,
        });
      },
      (err) => {
        setLocating(false);
        setNote(
          err.code === err.PERMISSION_DENIED
            ? "No problem, location stays off. Search for a city instead."
            : "We couldn't get your location. Search for a city instead.",
        );
        searchRef.current?.focus();
      },
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 10 * 60_000 },
    );
  }, [tryPlace]);

  const data = shown.data;
  const shownAmbiguous = shown.error instanceof ApiError && shown.error.status === 409;
  // The shown place itself can be ambiguous (e.g. ?place=Aurangabad): offer its choices too.
  const shownChoices =
    shownAmbiguous && dismissed !== placeKey(place!) ? (shown.error as ApiError).candidates : [];
  const choices = candidates.length ? candidates : shownChoices;

  return (
    <div className={`flex min-h-screen flex-col transition-[padding] duration-300 motion-reduce:transition-none ${assistantOpen ? "xl:pr-[420px]" : ""}`}>
      <Header
        timeZone={data?.location.timezone}
        placeName={data?.location.name}
        searchRef={searchRef}
        onSearch={(name) => void tryPlace({ kind: "place", name })}
        onUseLocation={useLocation}
        locating={locating}
        searching={searching}
        candidates={choices}
        onPickCandidate={(c) => void tryPlace({ kind: "place", name: c.formatted })}
        onDismissCandidates={() => {
          setCandidates([]);
          if (place) setDismissed(placeKey(place));
        }}
        message={note}
      />

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 pb-10 pt-6 sm:px-6 lg:px-8" aria-busy={shown.isFetching}>
        {place === undefined || (place && shown.isPending && !shownAmbiguous) ? (
          <DashboardSkeleton />
        ) : !place || shownAmbiguous ? (
          <WelcomeCard onUseLocation={useLocation} onSearch={() => searchRef.current?.focus()} locating={locating} />
        ) : data ? (
          <DashboardView data={data} stale={shown.isRefetchError} />
        ) : shown.isError ? (
          <ErrorCard message={errorMessage(shown.error)} onRetry={() => void shown.refetch()} />
        ) : null}
      </main>
      <Footer />

      <div id="assistant-slot" className="fixed right-(--ask-button-offset) bottom-(--ask-button-offset) z-30">
        <AssistantButton
          ref={assistantButton}
          open={assistantOpen}
          panelLoaded={assistantLoaded}
          onClick={() => {
            setAssistantLoaded(true);
            setAssistantOpen(true);
          }}
        />
        {assistantLoaded && (
          <AssistantPanel
            data={data}
            open={assistantOpen}
            onClose={() => setAssistantOpen(false)}
            buttonRef={assistantButton}
          />
        )}
      </div>
    </div>
  );
}

// The last card in a column takes up the rest of it, so both columns end level.
const FILL = "lg:order-none lg:flex-1 lg:[&>section]:h-full";

/**
 * Two columns on large screens; one on phones, in reading order: alerts, hero, best time, hourly,
 * air quality, 7-day, details, sunrise and sunset. The column wrappers dissolve on phones (display: contents) so each
 * card can take its own place in that order. On large screens both columns end level: the last card in
 * each one stretches, and sunrise and sunset spans the full width below.
 */
function DashboardView({ data, stale }: { data: DashboardData; stale: boolean }) {
  const tz = data.location.timezone;
  return (
    <div className="flex flex-col gap-6">
      {stale && (
        <p role="status" className="text-sm text-ink-soft">
          Couldn&apos;t refresh just now. Showing the forecast from {formatTime(data.current.time, tz)}.
        </p>
      )}
      <AlertsBanner alerts={data.alerts ?? []} timeZone={tz} />
      <div className="flex flex-col gap-6 lg:grid lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="contents lg:flex lg:flex-col lg:gap-6">
          <div className="order-1 lg:order-none"><Hero data={data} /></div>
          <div className="order-3 lg:order-none"><HourlyStrip hours={data.hourly} timeZone={tz} /></div>
          <WhenVisible className={`order-5 ${FILL}`} reserve="27rem"><DailyForecast daily={data.daily} /></WhenVisible>
        </div>
        <div className="contents lg:flex lg:flex-col lg:gap-6">
          <div className="order-2 lg:order-none"><BestTimeCard outdoor={data.outdoor_today} timeZone={tz} /></div>
          <WhenVisible className="order-4 lg:order-none" reserve="26rem">
            <AirQualityCard air={data.air_quality} timeZone={tz} />
          </WhenVisible>
          <WhenVisible className={`order-6 ${FILL}`} reserve="24rem">
            <DetailTiles current={data.current} today={data.daily[0]} />
          </WhenVisible>
        </div>
      </div>
      <WhenVisible reserve="10rem">
        <SunCard current={data.current} now={data.local_time} timeZone={tz} />
      </WhenVisible>
    </div>
  );
}
