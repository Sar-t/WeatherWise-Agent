/** Typed client for the WeatherWise API (types generated from ../openapi.json: npm run gen:api). */

import type { components } from "./api-types";
import { getClientId } from "./client-id";

export type Dashboard = components["schemas"]["DashboardOut"];
/** A choice offered with a 409 ambiguous_place error (in error.details.candidates). */
export type Candidate = { name: string; formatted: string; state?: string | null; country?: string | null; lat: number; lon: number };
export type DashboardAir = components["schemas"]["DashboardAir"];
export type HourOut = components["schemas"]["HourOut"];
export type DayOut = components["schemas"]["DayOut"];
export type CurrentOut = components["schemas"]["CurrentOut"];
export type AlertOut = components["schemas"]["AlertOut"];
export type OutdoorOut = components["schemas"]["OutdoorOut"];
export type ChatRequest = components["schemas"]["ChatRequest"];
export type ChatResponse = components["schemas"]["ChatResponse"];
export type AssessmentOut = components["schemas"]["AssessmentOut"];
export type AirQualityOut = components["schemas"]["AirQualityCard"];
export type SourceOut = components["schemas"]["SourceOut"];
export type ConversationOut = components["schemas"]["ConversationOut"];
export type ConversationDetail = components["schemas"]["ConversationDetail"];

export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

/** An error response from the API, in its one JSON shape. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryAfter: number | null = null,
    readonly candidates: Candidate[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Where to load the dashboard for: the device position (rounded) or a place name. */
export type Place =
  | { kind: "coords"; lat: number; lon: number }
  | { kind: "place"; name: string };

export function placeKey(place: Place): string {
  return place.kind === "coords" ? `${place.lat.toFixed(2)},${place.lon.toFixed(2)}` : place.name.trim().toLowerCase();
}

/** Sends a request with the client ID; any failure (network or non-2xx) becomes an ApiError. */
export async function send(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { "X-Client-Id": getClientId(), Accept: "application/json", ...init?.headers },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_error", "Can't reach WeatherWise right now. Check your connection.");
  }
  if (response.ok) return response;

  let body: { error?: { code?: string; message?: string; details?: { candidates?: Candidate[] } } } = {};
  try {
    body = await response.json();
  } catch {
    // not JSON: fall through with a generic message
  }
  const retry = response.headers.get("Retry-After");
  throw new ApiError(
    response.status,
    body.error?.code ?? "http_error",
    body.error?.message ?? "Something went wrong. Please try again.",
    retry ? Number(retry) : null,
    body.error?.details?.candidates ?? [],
  );
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return (await (await send(path, init)).json()) as T;
}

export function fetchDashboard(place: Place, signal?: AbortSignal): Promise<Dashboard> {
  const query =
    place.kind === "coords"
      ? `lat=${place.lat.toFixed(2)}&lon=${place.lon.toFixed(2)}` // never send more than ~1 km precision
      : `place=${encodeURIComponent(place.name.trim())}`;
  return request<Dashboard>(`/api/v1/dashboard?${query}`, { signal });
}
