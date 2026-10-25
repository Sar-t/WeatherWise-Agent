"use client";

import { useCallback, useRef, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, fetchDashboard, placeKey, type Candidate, type Place } from "@/lib/api";
import { loadPlace, savePlace } from "@/lib/location-store";
import { AirQualityCard } from "./AirQualityCard";
import { Header } from "./Header";
import { Hero } from "./Hero";
import { WelcomeCard } from "./WelcomeCard";
import { DashboardSkeleton, ErrorCard } from "./States";

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
    <div className="flex min-h-screen flex-col">
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

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 pb-24 pt-6 sm:px-6 lg:px-8" aria-busy={shown.isFetching}>
        {place === undefined || (place && shown.isPending && !shownAmbiguous) ? (
          <DashboardSkeleton />
        ) : !place || shownAmbiguous ? (
          <WelcomeCard onUseLocation={useLocation} onSearch={() => searchRef.current?.focus()} locating={locating} />
        ) : shown.isError ? (
          <ErrorCard message={errorMessage(shown.error)} onRetry={() => void shown.refetch()} />
        ) : data ? (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
            <div className="flex flex-col gap-6">
              <Hero data={data} />
            </div>
            <aside className="flex flex-col gap-6" aria-label="Air quality and outdoor outlook">
              <AirQualityCard air={data.air_quality} timeZone={data.location.timezone} />
            </aside>
          </div>
        ) : null}
      </main>

      {/* Reserved for the assistant (step 5b): a floating button bottom-right. */}
      <div id="assistant-slot" className="fixed bottom-6 right-6 z-30" />
    </div>
  );
}
