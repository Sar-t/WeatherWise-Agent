import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import delhi from "@/test/fixtures/dashboard-delhi.json";
import { API_BASE_URL, ApiError, fetchDashboard } from "./api";
import { earlyDashboardScript } from "./early-fetch";

const fetchMock = vi.fn<typeof fetch>();
const runHeadScript = () => new Function(earlyDashboardScript(API_BASE_URL))();
const urls = () => fetchMock.mock.calls.map(([url]) => String(url));

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => new Response(JSON.stringify(delhi), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  window.history.replaceState({}, "", "/");
});

describe("the early dashboard request", () => {
  it("is the app's own request for ?place=, made once and handed over", async () => {
    window.history.replaceState({}, "", "/?place=%20New%20Delhi%20");
    runHeadScript();
    expect(urls()).toEqual([`${API_BASE_URL}/api/v1/dashboard?place=New%20Delhi`]);
    const [, init] = fetchMock.mock.calls[0];
    const clientId = window.localStorage.getItem("ww.clientId");
    expect(clientId).toMatch(/^[0-9a-f-]{36}$/);
    expect(init?.headers).toEqual({ "X-Client-Id": clientId, Accept: "application/json" });

    const data = await fetchDashboard({ kind: "place", name: " New Delhi " });
    expect(data.location.name).toBe("New Delhi");
    expect(fetchMock).toHaveBeenCalledTimes(1); // no second request

    await fetchDashboard({ kind: "place", name: "New Delhi" }); // later requests go to the network as usual
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("uses the saved place, with coordinates at 2 decimals and the existing client ID", async () => {
    window.localStorage.setItem("ww.clientId", "11111111-2222-4333-8444-555555555555");
    window.localStorage.setItem("ww.lastPlace", JSON.stringify({ kind: "coords", lat: 28.6139, lon: 77.209 }));
    runHeadScript();
    expect(urls()).toEqual([`${API_BASE_URL}/api/v1/dashboard?lat=28.61&lon=77.21`]);
    expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({ "X-Client-Id": "11111111-2222-4333-8444-555555555555" });

    await fetchDashboard({ kind: "coords", lat: 28.6139, lon: 77.209 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does nothing on a first visit", () => {
    runHeadScript();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("isn't used for a different place", async () => {
    window.history.replaceState({}, "", "/?place=Delhi");
    runHeadScript();
    await fetchDashboard({ kind: "place", name: "Pune" });
    expect(urls()).toEqual([`${API_BASE_URL}/api/v1/dashboard?place=Delhi`, `${API_BASE_URL}/api/v1/dashboard?place=Pune`]);
  });

  it("reports a failed early request like any other network failure", async () => {
    fetchMock.mockImplementation(() => Promise.reject(new TypeError("Failed to fetch")));
    window.history.replaceState({}, "", "/?place=Delhi");
    runHeadScript();
    await expect(fetchDashboard({ kind: "place", name: "Delhi" })).rejects.toEqual(
      new ApiError(0, "network_error", "Can't reach WeatherWise right now. Check your connection."),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
