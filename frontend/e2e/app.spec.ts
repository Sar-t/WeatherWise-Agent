import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

/** Scroll to the end so the cards rendered on approach (below the fold on phones) are all there. */
async function renderWholeDashboard(page: Page) {
  await expect(page.getByRole("heading", { level: 1, name: "New Delhi" })).toBeVisible();
  for (let i = 0; i < 6; i++) {
    await page.mouse.wheel(0, 900);
    await page.waitForTimeout(150);
  }
  await expect(page.getByRole("heading", { name: "Sunrise and sunset" })).toBeAttached();
}

/** Serious and critical axe findings only (WCAG 2.x A and AA rules plus best practices). */
async function seriousViolations(page: Page) {
  const { violations } = await new AxeBuilder({ page }).analyze();
  return violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).join(", ")})`);
}

async function askAndWait(page: Page, question: string) {
  const box = page.getByRole("textbox", { name: "Your question" });
  await box.fill(question);
  await box.press("Enter");
  await expect(page.getByRole("region", { name: "Running in New Delhi" })).toBeVisible();
}

test("loads the dashboard for ?place=Delhi, with credits in the footer", async ({ page, api }) => {
  await page.goto("/?place=Delhi");
  await expect(page.getByRole("heading", { level: 1, name: "New Delhi" })).toBeVisible();
  for (const name of ["Best time to step out", "Next 24 hours"]) {
    await expect(page.getByRole("heading", { name })).toBeVisible();
  }
  await renderWholeDashboard(page);
  for (const name of ["Air quality", "7-day forecast", "Right now", "Sunrise and sunset"]) {
    await expect(page.getByRole("heading", { name })).toBeAttached();
  }
  expect(api.calls).toEqual(["GET /api/v1/dashboard?place=Delhi"]); // one request, though it starts before hydration

  const footer = page.getByRole("contentinfo");
  await expect(footer).toContainText("Weather and air quality data © OpenWeather");
  await expect(footer).toContainText("Geocoding powered by Geoapify");
  await expect(footer.getByRole("link", { name: /OpenStreetMap contributors/ })).toHaveAttribute(
    "href",
    "https://www.openstreetmap.org/copyright",
  );
  await expect(page).toHaveTitle("WeatherWise: weather, air quality and the best time to step out");
});

test("searching an ambiguous city offers choices and loads the one picked", async ({ page }) => {
  await page.goto("/?place=Delhi");
  await expect(page.getByRole("heading", { level: 1, name: "New Delhi" })).toBeVisible();

  const search = page.getByRole("searchbox", { name: "Search for a city" });
  await search.fill("Aurangabad");
  await search.press("Enter");
  const choices = page.getByRole("dialog", { name: "Did you mean" });
  await expect(choices.getByRole("button")).toHaveCount(4); // 3 places and Close
  await choices.getByRole("button", { name: /Chhatrapati Sambhajinagar/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Chhatrapati Sambhajinagar" })).toBeVisible();
});

test("the assistant streams an answer with its assessment and a source", async ({ page, api }) => {
  await page.goto("/?place=Delhi");
  await page.getByRole("button", { name: "Ask WeatherWise" }).click();
  const panel = page.getByRole("dialog", { name: "Ask WeatherWise" });
  await expect(panel.getByText("Asking about New Delhi")).toBeVisible();

  await askAndWait(page, "Can I go for a run in Delhi tomorrow evening?");
  const source = panel.getByRole("link", { name: /NAQI Moderately Polluted \(101-200\)/ });
  await expect(source).toHaveAttribute("target", "_blank");
  await expect(source).toHaveAttribute("rel", "noopener noreferrer");
  await expect(panel.getByText("Worst in window:")).toBeVisible();
  expect(api.chatBodies[0]).toMatchObject({
    message: "Can I go for a run in Delhi tomorrow evening?",
    location: { lat: 28.61, lon: 77.21 },
    timezone: "Asia/Kolkata",
  });
});

test("keyboard only: header, cards, footer and the assistant, with visible focus", async ({ page, isMobile }) => {
  test.skip(isMobile, "phones have no Tab key; covered on desktop");
  await page.goto("/?place=Delhi");
  await expect(page.getByRole("heading", { level: 1, name: "New Delhi" })).toBeVisible();

  const stops: string[] = [];
  for (let i = 0; i < 40 && stops.at(-1) !== "Ask WeatherWise"; i++) {
    await page.keyboard.press("Tab");
    const stop = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement;
      const name = el.getAttribute("aria-label") || el.textContent?.replace(/\s*\(opens in a new tab\)/, "").trim() || el.tagName;
      // Fields show focus on their wrapper (a coloured border and ring); everything else has an outline.
      const shown = el.tagName === "INPUT" ? getComputedStyle(el.closest("div")!).borderColor : getComputedStyle(el).outlineStyle;
      return { name, shown };
    });
    expect(stop.shown, `no visible focus on ${stop.name}`).not.toBe("none");
    stops.push(stop.name);
  }
  const inOrder = ["WeatherWise home", "Use my location", "Later hours", "Hourly forecast, scrolls sideways", "OpenWeather",
    "About WeatherWise and privacy", "Ask WeatherWise"];
  expect(stops.filter((s) => inOrder.includes(s))).toEqual(inOrder);

  await page.keyboard.press("Enter");
  const box = page.getByRole("textbox", { name: "Your question" });
  await expect(box).toBeFocused();
  await page.keyboard.type("Can I go for a run in Delhi tomorrow evening?");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("region", { name: "Running in New Delhi" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Ask WeatherWise" })).toBeFocused();
});

test("no serious or critical accessibility violations on the dashboard", async ({ page }) => {
  await page.goto("/?place=Delhi");
  await renderWholeDashboard(page);
  expect(await seriousViolations(page)).toEqual([]);
});

test("no serious or critical accessibility violations with the assistant open", async ({ page }) => {
  await page.goto("/?place=Delhi");
  await page.getByRole("button", { name: "Ask WeatherWise" }).click();
  await askAndWait(page, "Can I go for a run in Delhi tomorrow evening?");
  expect(await seriousViolations(page)).toEqual([]);
});

test("the About page and the 404 page", async ({ page }) => {
  await page.goto("/about");
  await expect(page).toHaveTitle("About · WeatherWise");
  await expect(page.getByRole("heading", { name: "Privacy" })).toBeVisible();
  await expect(page.getByRole("contentinfo")).toContainText("© OpenWeather");

  const response = await page.goto("/no-such-page");
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "This page isn't here" })).toBeVisible();
  await page.getByRole("link", { name: "Go to the dashboard" }).click();
  await expect(page).toHaveURL("/");
});
