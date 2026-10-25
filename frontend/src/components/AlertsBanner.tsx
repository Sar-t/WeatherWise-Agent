import type { AlertOut } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { UiIcon } from "./icons/UiIcon";

/** Official weather alerts for the place. Nothing renders when there are none. */
export function AlertsBanner({ alerts, timeZone }: { alerts: AlertOut[]; timeZone: string }) {
  if (!alerts.length) return null;
  return (
    <section
      aria-labelledby="alerts-title"
      className="rounded-[var(--radius-card)] border border-amber-300 bg-amber-50 px-5 py-4 text-amber-950 shadow-card"
    >
      <h2 id="alerts-title" className="flex items-center gap-2 text-[0.95rem] font-semibold">
        <UiIcon name="alert" className="shrink-0 text-amber-700" />
        {alerts.length === 1 ? "Weather alert" : `${alerts.length} weather alerts`}
      </h2>
      <ul className="mt-2 flex flex-col gap-2">
        {alerts.map((a) => (
          <li key={`${a.event}-${a.start}`} className="pl-7">
            <p className="font-medium">{a.event}</p>
            <p className="tabular text-sm text-amber-900">
              {formatDateTime(a.start, timeZone)} – {formatDateTime(a.end, timeZone)}
              <span aria-hidden className="mx-1.5">·</span>
              {a.sender}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
