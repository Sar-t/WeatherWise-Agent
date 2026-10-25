"use client";

import { UiIcon } from "./icons/UiIcon";

/** Skeletons in the shape of the real cards (no spinners). */
export function DashboardSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]" aria-label="Loading the forecast" role="status">
      <div className="rounded-[1.75rem] border border-line/70 bg-surface p-8 shadow-card">
        <div className="skeleton h-8 w-48" />
        <div className="skeleton mt-3 h-4 w-32" />
        <div className="skeleton mt-10 h-24 w-56" />
        <div className="skeleton mt-6 h-5 w-40" />
        <div className="skeleton mt-3 h-4 w-60" />
      </div>
      <div className="rounded-[var(--radius-card)] border border-line/80 bg-surface p-6 shadow-card">
        <div className="skeleton h-4 w-24" />
        <div className="skeleton mt-5 h-14 w-32" />
        <div className="mt-6 grid grid-cols-3 gap-3">
          <div className="skeleton h-9" />
          <div className="skeleton h-9" />
          <div className="skeleton h-9" />
        </div>
        <div className="skeleton mt-6 h-20" />
      </div>
      <span className="sr-only">Loading…</span>
    </div>
  );
}

export function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section role="alert" className="mx-auto max-w-lg rounded-[var(--radius-card)] border border-line bg-surface p-8 text-center shadow-card">
      <p className="font-display text-2xl font-medium">We couldn&apos;t load the forecast</p>
      <p className="mt-3 text-ink-soft">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-6 inline-flex h-11 items-center gap-2 rounded-full bg-accent px-5 text-sm font-semibold text-white hover:bg-accent-strong"
      >
        <UiIcon name="refresh" />
        Try again
      </button>
    </section>
  );
}
