import type { OutdoorOut } from "@/lib/api";
import { VERDICT_LABELS, timeRange } from "@/lib/details";
import { Card, CardTitle } from "./Card";

const VERDICT_STYLES: Record<OutdoorOut["verdict"], string> = {
  suitable: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  marginal: "bg-amber-50 text-amber-900 ring-amber-200",
  not_suitable: "bg-red-50 text-red-800 ring-red-200",
};

/** The general outdoor outlook: the best window for the rest of today, or tomorrow when today is nearly over. */
export function BestTimeCard({ outdoor, timeZone }: { outdoor: OutdoorOut | null | undefined; timeZone: string }) {
  if (!outdoor) {
    return (
      <Card>
        <CardTitle>Best time to step out</CardTitle>
        <p className="mt-4 text-[0.95rem] text-ink-soft">The outdoor outlook isn&apos;t available right now.</p>
      </Card>
    );
  }
  const block = outdoor.best_block;
  const reasons = block?.reasons?.length ? block.reasons : outdoor.reason ? [outdoor.reason] : [];

  return (
    <Card>
      <CardTitle
        meta={
          <span className="rounded-full bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-accent-strong">
            {outdoor.day === "tomorrow" ? "Tomorrow" : "Today"}
          </span>
        }
      >
        Best time to step out
      </CardTitle>

      {block ? (
        <>
          {/* A not-suitable day still has a least-bad window; don't present it as a recommendation. */}
          <p className="mt-4 text-xs text-muted">
            {outdoor.verdict === "not_suitable" ? "Least risky window" : "Best window"}
          </p>
          <p className="tabular font-display text-[2.1rem] leading-tight font-light tracking-tight">
            {timeRange(block.start, block.end, timeZone)}
          </p>
        </>
      ) : (
        <p className="mt-4 font-display text-2xl leading-tight font-light">No good window</p>
      )}

      <p className={`mt-3 inline-flex rounded-full px-3 py-1 text-sm font-semibold ring-1 ${VERDICT_STYLES[outdoor.verdict]}`}>
        {VERDICT_LABELS[outdoor.verdict]}
      </p>

      {reasons.length > 0 && (
        <ul className="mt-4 flex flex-col gap-1.5 text-sm text-ink-soft">
          {reasons.map((r) => (
            <li key={r} className="flex gap-2">
              <span aria-hidden className="mt-[0.55rem] h-1 w-1 shrink-0 rounded-full bg-muted" />
              {r[0].toUpperCase() + r.slice(1)}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
