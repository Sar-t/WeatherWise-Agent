import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PARIS_ALERTS } from "@/test/fixtures/alerts-paris";
import { AlertsBanner } from "./AlertsBanner";

describe("AlertsBanner", () => {
  it("shows each alert with its event, time range and source", () => {
    render(<AlertsBanner alerts={PARIS_ALERTS} timeZone="Europe/Paris" />);
    const banner = screen.getByRole("region", { name: "2 weather alerts" });
    const items = within(banner).getAllByRole("listitem");
    expect(items).toHaveLength(2);

    expect(items[0]).toHaveTextContent("Moderate wind warning");
    expect(items[1]).toHaveTextContent("Moderate rain-flood warning");
    for (const item of items) {
      // Shown in the place's own time: Sun 25 Oct 06:00 until Mon 26 Oct 00:00.
      expect(item).toHaveTextContent(/Sun, 25 Oct, 06:00\s*–\s*Mon, 26 Oct, 00:00/);
      expect(item).toHaveTextContent("METEO-FRANCE");
    }
  });

  it("names a single alert in the singular", () => {
    render(<AlertsBanner alerts={PARIS_ALERTS.slice(0, 1)} timeZone="Europe/Paris" />);
    expect(screen.getByRole("region", { name: "Weather alert" })).toBeInTheDocument();
  });

  it("renders nothing when there are no alerts", () => {
    const { container } = render(<AlertsBanner alerts={[]} timeZone="Europe/Paris" />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
});
