/**
 * SYNTHETIC FIXTURE, based on real alerts. The live Paris dashboard on 25 Oct 2026 carried these two
 * Météo-France warnings (seen in the 5a screenshot round), but they expired before a response could
 * be saved. Event names, sender and times are copied from that screenshot; the shape is the API's
 * AlertOut. Times are Paris local (UTC+1 after the clocks went back that morning).
 */

import type { AlertOut } from "@/lib/api";

export const PARIS_ALERTS: AlertOut[] = [
  {
    event: "Moderate wind warning",
    sender: "METEO-FRANCE",
    start: "2026-10-25T06:00:00+01:00",
    end: "2026-10-26T00:00:00+01:00",
  },
  {
    event: "Moderate rain-flood warning",
    sender: "METEO-FRANCE",
    start: "2026-10-25T06:00:00+01:00",
    end: "2026-10-26T00:00:00+01:00",
  },
];
