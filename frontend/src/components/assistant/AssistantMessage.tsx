/** One assistant reply: plain text, then assessment cards, air quality chips and numbered sources.
 * Everything is rendered as text (React escapes it); no HTML or markdown is ever interpreted. */

import type { AirQualityOut, AssessmentOut, ChatResponse, SourceOut } from "@/lib/api";
import { naqiStyle, owmStyle } from "@/lib/aqi";
import { timeRange } from "@/lib/details";
import { formatDay, keepUnits } from "@/lib/format";
import { VERDICT_STYLES } from "../BestTimeCard";

const BULLET = /^\s*[-•*]\s+/;

/** Paragraphs split on blank lines; runs of "- " / "• " lines become a list (comparisons). */
export function ReplyText({ text }: { text: string }) {
  const blocks = text.trim().split(/\n\s*\n/);
  return (
    <div className="flex flex-col gap-3">
      {blocks.map((block, i) => {
        const groups: { bullets: boolean; lines: string[] }[] = [];
        for (const line of block.split("\n")) {
          const bullets = BULLET.test(line);
          const last = groups.at(-1);
          if (last && last.bullets === bullets) last.lines.push(line);
          else groups.push({ bullets, lines: [line] });
        }
        return groups.map((g, j) =>
          g.bullets ? (
            <ul key={`${i}-${j}`} className="flex flex-col gap-1.5 pl-1">
              {g.lines.map((line, k) => (
                <li key={k} className="flex gap-2">
                  <span aria-hidden className="mt-[0.6rem] h-1 w-1 shrink-0 rounded-full bg-muted" />
                  <span>{keepUnits(line.replace(BULLET, ""))}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p key={`${i}-${j}`} className="whitespace-pre-line">
              {keepUnits(g.lines.join("\n"))}
            </p>
          ),
        );
      })}
    </div>
  );
}

const ASSESSMENT_VERDICTS: Record<AssessmentOut["verdict"], string> = {
  suitable: "Good",
  marginal: "Possible with care",
  not_suitable: "Not advised",
};

function activityName(a: AssessmentOut): string {
  const name = (a.activity || a.profile).replace(/_/g, " ");
  return name ? name[0].toUpperCase() + name.slice(1) : "Outdoors";
}

export function AssessmentCard({ a }: { a: AssessmentOut }) {
  const tz = a.location.timezone;
  const best = a.best_blocks?.[0];
  return (
    <section aria-label={`${activityName(a)} in ${a.location.name}`} className="rounded-2xl border border-line bg-canvas/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-ink">
          {activityName(a)}
          <span className="font-normal text-muted"> · {a.location.name}</span>
        </p>
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ${VERDICT_STYLES[a.verdict]}`}>
          {ASSESSMENT_VERDICTS[a.verdict]}
        </span>
      </div>
      <dl className="tabular mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted">Score</dt>
        <dd>{a.score} of 100</dd>
        {best && (
          <>
            <dt className="text-muted">Best window</dt>
            <dd>
              {formatDay(best.start, tz)}, {timeRange(best.start, best.end, tz)}
            </dd>
          </>
        )}
      </dl>
      {(a.hints?.length ?? 0) > 0 && (
        <ul className="mt-2 flex flex-col gap-1 text-sm text-ink-soft">
          {a.hints!.slice(0, 2).map((hint) => (
            <li key={hint} className="flex gap-2">
              <span aria-hidden className="mt-[0.55rem] h-1 w-1 shrink-0 rounded-full bg-muted" />
              <span>{keepUnits(hint)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function AirChip({ air }: { air: AirQualityOut }) {
  const style = air.scale === "naqi" ? naqiStyle(air.category) : owmStyle(air.value);
  const value = air.value == null ? "–" : air.scale === "naqi" ? `NAQI ${air.value}` : `Level ${air.value}/5`;
  return (
    <p className="inline-flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted">Air in {air.location.name}</span>
      <span
        className="tabular rounded-full px-2.5 py-0.5 font-semibold"
        style={style ? { background: style.background, color: style.text } : undefined}
      >
        {value}
        {style ? ` · ${style.label}` : air.category ? ` · ${air.category}` : ""}
      </span>
    </p>
  );
}

export function Sources({ sources }: { sources: SourceOut[] }) {
  return (
    <div className="border-t border-line-soft pt-3">
      <p className="text-xs font-semibold text-muted">Sources</p>
      <ol className="mt-1.5 flex flex-col gap-1.5 text-sm">
        {sources.map((s, i) => (
          <li key={s.url} className="flex gap-2">
            <span className="tabular text-muted">{i + 1}.</span>
            <a
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent-strong underline decoration-accent/30 underline-offset-2 hover:decoration-accent"
            >
              {s.title}
              <span className="text-muted no-underline"> · {s.source_name}</span>
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** The extras that come with a final reply. */
export function ReplyExtras({ response }: { response: ChatResponse }) {
  const assessments = response.assessments ?? [];
  const air = response.air_quality ?? [];
  const sources = response.sources ?? [];
  if (!assessments.length && !air.length && !sources.length) return null;
  return (
    <div className="mt-3 flex flex-col gap-3">
      {assessments.map((a, i) => (
        <AssessmentCard key={`${a.location.name}-${a.activity}-${i}`} a={a} />
      ))}
      {air.map((a, i) => (
        <AirChip key={`${a.location.name}-${i}`} air={a} />
      ))}
      {sources.length > 0 && <Sources sources={sources} />}
    </div>
  );
}
