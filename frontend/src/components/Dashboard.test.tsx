import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ambiguous from "@/test/fixtures/ambiguous-aurangabad.json";
import delhi from "@/test/fixtures/dashboard-delhi.json";
import { Dashboard } from "./Dashboard";

let search = "";
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(search) }));

const fetchMock = vi.fn<typeof fetch>();

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } }),
  );
}

function errorBody(code: string, message: string) {
  return { error: { code, message, request_id: "test" } };
}

function renderDashboard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Dashboard />
    </QueryClientProvider>,
  );
}

function requestedUrls(): string[] {
  return fetchMock.mock.calls.map(([url]) => String(url));
}

function mockGeolocation(getCurrentPosition: Geolocation["getCurrentPosition"]) {
  Object.defineProperty(navigator, "geolocation", { configurable: true, value: { getCurrentPosition } });
}

beforeEach(() => {
  search = "";
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  mockGeolocation(vi.fn());
});

describe("first and returning visits", () => {
  it("welcomes a first visit without asking for location or calling the API", () => {
    renderDashboard();
    expect(screen.getByText(/Weather, air quality and the best time to step out/)).toBeInTheDocument();
    expect(navigator.geolocation.getCurrentPosition).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("loads the saved place straight away on a return visit", async () => {
    window.localStorage.setItem("ww.lastPlace", JSON.stringify({ kind: "place", name: "Delhi" }));
    fetchMock.mockImplementation(() => json(delhi));
    renderDashboard();

    expect(await screen.findByRole("heading", { level: 1, name: "New Delhi" })).toBeInTheDocument();
    expect(requestedUrls()[0]).toContain("/api/v1/dashboard?place=Delhi");
    for (const title of ["Best time to step out", "Next 24 hours", "Air quality", "7-day forecast", "Right now"]) {
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    }
    // Today's rain comes from the daily forecast, in the "Right now" tiles.
    const rainToday = Math.round(delhi.daily[0].rain_prob_pct ?? 0);
    expect(screen.getByText("Rain today").parentElement).toHaveTextContent(`Rain today${rainToday}%`);
    // Every chart has a text alternative.
    expect(screen.getByText(/^Next 24 hours: high/)).toBeInTheDocument();
    expect(screen.getByText(/^Next 7 days:/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Sunrise \d\d:\d\d, sunset/ })).toBeInTheDocument();
  });

  it("prefers ?place= over the saved place", async () => {
    search = "place=Delhi";
    window.localStorage.setItem("ww.lastPlace", JSON.stringify({ kind: "place", name: "Pune" }));
    fetchMock.mockImplementation(() => json(delhi));
    renderDashboard();
    await screen.findByRole("heading", { level: 1, name: "New Delhi" });
    expect(requestedUrls()).toEqual([expect.stringContaining("place=Delhi")]);
  });
});

describe("loading and errors", () => {
  beforeEach(() => {
    window.localStorage.setItem("ww.lastPlace", JSON.stringify({ kind: "place", name: "Delhi" }));
  });

  it("shows a skeleton, not a spinner, while loading", () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    renderDashboard();
    expect(screen.getByRole("status", { name: "Loading the forecast" })).toBeInTheDocument();
  });

  it("shows an error card whose retry loads the dashboard", async () => {
    fetchMock.mockImplementationOnce(() => json(errorBody("internal_error", "Something went wrong on our side."), 500));
    renderDashboard();
    expect(await screen.findByRole("alert")).toHaveTextContent("We couldn't load the forecast");

    fetchMock.mockImplementation(() => json(delhi));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { level: 1, name: "New Delhi" })).toBeInTheDocument();
  });

  it("explains a 429 plainly", async () => {
    fetchMock.mockImplementation(() =>
      json(errorBody("rate_limited", "Too many dashboard requests; please wait a moment."), 429, { "Retry-After": "20" }),
    );
    renderDashboard();
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests, try again in a moment.");
  });
});

describe("ambiguous places (409)", () => {
  it("offers the candidates and loads the one picked", async () => {
    search = "place=Aurangabad";
    fetchMock.mockImplementation((url) =>
      String(url).includes("Aurangabad") && !String(url).includes("%2C") ? json(ambiguous, 409) : json(delhi),
    );
    renderDashboard();

    const dialog = await screen.findByRole("dialog", { name: "Did you mean" });
    const first = ambiguous.error.details.candidates[0];
    await userEvent.click(within(dialog).getByRole("button", { name: new RegExp(first.name) }));

    await screen.findByRole("heading", { level: 1 });
    expect(requestedUrls().at(-1)).toContain(`place=${encodeURIComponent(first.formatted)}`);
  });
});

describe("Use my location", () => {
  it("on denial, says so kindly and moves focus to search", async () => {
    mockGeolocation((_ok, fail) =>
      fail?.({ code: 1, PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3, message: "denied" } as GeolocationPositionError),
    );
    renderDashboard();
    await userEvent.click(screen.getAllByRole("button", { name: "Use my location" })[0]);

    expect(screen.getByText("No problem, location stays off. Search for a city instead.")).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search for a city" })).toHaveFocus();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends and stores coordinates rounded to 2 decimals", async () => {
    mockGeolocation((ok) => ok({ coords: { latitude: 28.613939, longitude: 77.209023 } } as GeolocationPosition));
    fetchMock.mockImplementation(() => json(delhi));
    renderDashboard();
    await userEvent.click(screen.getAllByRole("button", { name: "Use my location" })[0]);

    await screen.findByRole("heading", { level: 1, name: "New Delhi" });
    expect(requestedUrls()[0]).toContain("lat=28.61&lon=77.21");
    await waitFor(() =>
      expect(JSON.parse(window.localStorage.getItem("ww.lastPlace")!)).toEqual({ kind: "coords", lat: 28.61, lon: 77.21 }),
    );
  });
});
