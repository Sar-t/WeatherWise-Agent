/**
 * The mocked backend for the e2e tests. Every request the browser makes is routed here: the app's own
 * files go through, API calls are answered from recorded fixtures (real responses saved from the
 * backend), and anything else is blocked. A blocked request fails the test, so a missing mock can
 * never reach a real API or use quota.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test as base, expect, type Request } from "@playwright/test";
import { API_HOST } from "../playwright.config";

const FIXTURES = join(__dirname, "..", "src", "test", "fixtures");
const fixture = (name: string) => readFileSync(join(FIXTURES, name), "utf-8");

const DELHI = fixture("dashboard-delhi.json");
const SAMBHAJINAGAR = fixture("dashboard-sambhajinagar.json"); // the first "Did you mean" choice
const AMBIGUOUS = fixture("ambiguous-aurangabad.json"); // the real 409 for "Aurangabad"
const STREAM = fixture("chat-stream-delhi-run.sse"); // a real streamed answer with an assessment and a source
const EMPTY_CONVERSATION = JSON.stringify(
  (JSON.parse(fixture("conversations.json")) as { title: string }[]).find((c) => !c.title),
); // a real conversation as POST /conversations returns it, before any message

const CORS = { "access-control-allow-origin": "*", "access-control-expose-headers": "Retry-After" };

type Reply = { status: number; body: string; contentType: string };
const json = (body: string, status = 200): Reply => ({ status, body, contentType: "application/json" });

/** The recorded answer for an API request, or null when there's none (the request is then blocked). */
function recorded(request: Request): Reply | null {
  const url = new URL(request.url());
  const route = `${request.method()} ${url.pathname}`;
  if (route === "GET /api/v1/dashboard") {
    const place = url.searchParams.get("place");
    if (place === "Delhi") return json(DELHI);
    if (place === "Aurangabad") return json(AMBIGUOUS, 409);
    if (place === "Chhatrapati Sambhajinagar, MH, India") return json(SAMBHAJINAGAR);
    return null;
  }
  if (route === "POST /api/v1/conversations") return json(EMPTY_CONVERSATION, 201);
  if (route === "POST /api/v1/chat/stream") return { status: 200, body: STREAM, contentType: "text/event-stream" };
  return null;
}

export type Api = {
  /** API requests answered from fixtures, as "METHOD /path?query". */
  calls: string[];
  /** The JSON bodies posted to the chat stream. */
  chatBodies: Record<string, unknown>[];
};

export const test = base.extend<{ api: Api }>({
  api: [
    async ({ context, baseURL }, use) => {
      const app = new URL(baseURL!).origin;
      const api: Api = { calls: [], chatBodies: [] };
      const blocked: string[] = [];

      await context.route("**/*", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin === app) return route.continue(); // the app's own pages, scripts, fonts, icons
        if (url.origin === API_HOST) {
          if (request.method() === "OPTIONS") {
            return route.fulfill({
              status: 204,
              headers: { ...CORS, "access-control-allow-headers": "*", "access-control-allow-methods": "GET, POST, DELETE" },
            });
          }
          const reply = recorded(request);
          if (reply) {
            api.calls.push(`${request.method()} ${url.pathname}${url.search}`);
            if (url.pathname === "/api/v1/chat/stream") api.chatBodies.push(request.postDataJSON());
            return route.fulfill({ ...reply, headers: CORS });
          }
        }
        blocked.push(`${request.method()} ${request.url()}`);
        return route.abort("blockedbyclient");
      });

      await use(api);
      expect(blocked, "requests with no recorded fixture were blocked").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
