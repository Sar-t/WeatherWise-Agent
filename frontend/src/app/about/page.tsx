import type { Metadata } from "next";
import type { ReactNode } from "react";
import { PageShell } from "@/components/PageShell";

export const metadata: Metadata = {
  title: "About",
  description:
    "What WeatherWise does, where its data comes from, how the assistant writes its answers, and how your privacy is protected.",
  alternates: { canonical: "/about" },
};

export default function AboutPage() {
  return (
    <PageShell>
      <h1 className="font-display text-[2.2rem] leading-tight font-medium tracking-tight sm:text-5xl">About WeatherWise</h1>
      <p className="mt-4 text-lg text-ink-soft">
        WeatherWise shows the weather, air quality and the best time to step out for any place, with a focus on
        India. Ask the assistant about plans like a run, a commute or a trip and it answers from the same forecast
        you see on the dashboard.
      </p>

      <div className="mt-8 flex flex-col gap-6">
        <Section title="Where the data comes from">
          <ul className="flex list-disc flex-col gap-2 pl-5">
            <li>
              <b className="font-semibold text-ink">Weather and air quality:</b> OpenWeather (current conditions,
              hourly and daily forecasts, alerts from national weather services, and air pollution readings).
            </li>
            <li>
              <b className="font-semibold text-ink">Air quality in India</b> is shown on CPCB&apos;s National Air
              Quality Index (NAQI), calculated from those readings. Elsewhere it&apos;s OpenWeather&apos;s 1–5 scale,
              labelled as such.
            </li>
            <li>
              <b className="font-semibold text-ink">Place search:</b> Geoapify, using OpenStreetMap data.
            </li>
            <li>
              <b className="font-semibold text-ink">Health and safety guidance:</b> official publications from CPCB,
              the Ministry of Health and Family Welfare (MoHFW), the World Health Organization (WHO) and the National
              Disaster Management Authority (NDMA). Answers cite the ones they use.
            </li>
          </ul>
        </Section>

        <Section title="How the assistant works">
          <p>
            Whether a time suits an activity is decided by fixed rules, not by AI: each hour is scored on the
            feels-like temperature, rain, wind and gusts, UV, visibility and air quality, while thunderstorms, heavy
            rain and severe weather alerts rule a time out. Those scores give the verdict and the best window. An AI model then writes the answer in plain language from that forecast data and the
            verdict. It&apos;s told to use only those numbers, and safety advice may only come from the official
            guidance above.
          </p>
          <p className="mt-3">Answers can still contain mistakes, so check anything important.</p>
        </Section>

        <Section title="Not medical advice">
          <p>
            WeatherWise gives general weather and air quality information. It is not medical advice. If you have a
            health condition, follow your doctor&apos;s guidance, and in an emergency contact local emergency
            services.
          </p>
        </Section>

        <Section title="Privacy">
          <ul className="flex list-disc flex-col gap-2 pl-5">
            <li>
              <b className="font-semibold text-ink">No account.</b> Your browser keeps a random, anonymous ID so the
              app can keep your conversations apart from other people&apos;s.
            </li>
            <li>
              <b className="font-semibold text-ink">Location only when you ask.</b> &ldquo;Use my location&rdquo;
              asks your browser first, and the position is rounded to about a kilometre before it leaves your device.
              It is never stored or logged more precisely.
            </li>
            <li>
              <b className="font-semibold text-ink">Conversations</b> are stored so the assistant can follow up on
              earlier questions. You can delete any of them from the history menu in the assistant.
            </li>
            <li>
              <b className="font-semibold text-ink">On this device</b> the browser also remembers the last place you
              looked at and your current conversation, so a refresh picks up where you left off.
            </li>
          </ul>
        </Section>
      </div>
    </PageShell>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-[var(--radius-card)] border border-line/80 bg-surface p-6 leading-relaxed text-ink-soft shadow-card">
      <h2 className="mb-3 font-display text-xl font-medium tracking-tight text-ink">{title}</h2>
      {children}
    </section>
  );
}
