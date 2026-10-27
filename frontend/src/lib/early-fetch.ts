/**
 * Starts the dashboard request from an inline <script> in the page head, before the JavaScript bundle
 * has even downloaded, then hands that response to the app's first request for the same URL. On a slow
 * phone this saves the whole download-and-hydrate time before data can arrive. One request either way:
 * the response is taken once, only for the identical URL.
 *
 * The script must mirror the app's own rules, kept in sync by early-fetch.test.ts:
 * which place to show (initialPlace in Dashboard.tsx, loadPlace), the URL (fetchDashboard), and the
 * anonymous client ID (getClientId).
 */

const KEY = "__wwEarlyDashboard";

type Early = { url: string; response: Promise<Response> };
declare global {
  interface Window {
    [KEY]?: Early;
  }
}

/** The inline script for the page head. `apiBase` is NEXT_PUBLIC_API_BASE_URL without a trailing slash. */
export function earlyDashboardScript(apiBase: string): string {
  return `(function () {
  try {
    var q = new URLSearchParams(location.search).get("place");
    var place = q && q.trim() ? { kind: "place", name: q.trim() } : null;
    if (!place) {
      var saved = JSON.parse(localStorage.getItem("ww.lastPlace") || "null");
      if (saved && saved.kind === "coords" && Number.isFinite(saved.lat) && Number.isFinite(saved.lon)) place = saved;
      else if (saved && saved.kind === "place" && typeof saved.name === "string" && saved.name.trim()) place = saved;
    }
    if (!place) return; // a first visit: the welcome card, nothing to load
    var query = place.kind === "coords"
      ? "lat=" + place.lat.toFixed(2) + "&lon=" + place.lon.toFixed(2)
      : "place=" + encodeURIComponent(place.name.trim());
    var id = localStorage.getItem("ww.clientId");
    if (!id) {
      if (!window.crypto || !crypto.randomUUID) return; // the app makes the ID itself
      id = crypto.randomUUID();
      localStorage.setItem("ww.clientId", id);
    }
    var url = ${JSON.stringify(apiBase)} + "/api/v1/dashboard?" + query;
    var response = fetch(url, { headers: { "X-Client-Id": id, Accept: "application/json" } });
    response.catch(function () {}); // a failure is reported when the app takes it, not as an unhandled rejection
    window.${KEY} = { url: url, response: response };
  } catch (e) {}
})();`;
}

/** The early response for `url`, at most once; null if there's none (or it was for another URL). */
export function takeEarlyResponse(url: string): Promise<Response> | null {
  if (typeof window === "undefined") return null;
  const early = window[KEY];
  if (!early || early.url !== url) return null;
  delete window[KEY];
  return early.response;
}
