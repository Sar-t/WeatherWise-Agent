import type { Metadata } from "next";
import Link from "next/link";
import { Mark } from "@/components/Brand";
import { PageShell } from "@/components/PageShell";

export const metadata: Metadata = { title: "Page not found" };

export default function NotFound() {
  return (
    <PageShell>
      <section className="rounded-[var(--radius-card)] border border-line/80 bg-surface p-8 text-center shadow-card sm:p-12">
        <Mark size={48} className="mx-auto text-accent" />
        <h1 className="mt-4 font-display text-3xl font-medium tracking-tight">This page isn&apos;t here</h1>
        <p className="mt-3 text-ink-soft">The link may be old or mistyped. The forecast is on the dashboard.</p>
        <Link
          href="/"
          className="mt-6 inline-flex h-11 items-center rounded-full bg-accent px-5 text-sm font-semibold text-white hover:bg-accent-strong"
        >
          Go to the dashboard
        </Link>
      </section>
    </PageShell>
  );
}
