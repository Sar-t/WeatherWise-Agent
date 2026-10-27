"use client";

import Link from "next/link";
import { useEffect } from "react";
import { Mark } from "./Brand";
import { PageShell } from "./PageShell";

/** What error.tsx and global-error.tsx show: a calm message, Try again, and a way home. */
export function ErrorPage({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    console.error(error); // the browser console only; the message isn't shown to the user
  }, [error]);

  return (
    <PageShell>
      <section role="alert" className="rounded-[var(--radius-card)] border border-line/80 bg-surface p-8 text-center shadow-card sm:p-12">
        <Mark size={48} className="mx-auto text-accent" />
        <h1 className="mt-4 font-display text-3xl font-medium tracking-tight">Something went wrong</h1>
        <p className="mt-3 text-ink-soft">This page hit an unexpected problem. Trying again usually helps.</p>
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <button
            type="button"
            onClick={() => retry()}
            className="inline-flex h-11 items-center rounded-full bg-accent px-5 text-sm font-semibold text-white hover:bg-accent-strong"
          >
            Try again
          </button>
          <Link
            href="/"
            className="inline-flex h-11 items-center rounded-full border border-line bg-surface px-5 text-sm font-semibold text-ink hover:border-accent hover:text-accent-strong"
          >
            Go to the dashboard
          </Link>
        </div>
      </section>
    </PageShell>
  );
}
