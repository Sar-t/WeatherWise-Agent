import Link from "next/link";
import type { ReactNode } from "react";
import { Wordmark } from "./Brand";
import { Footer } from "./Footer";

/** The frame for pages other than the dashboard: the wordmark, a way back, the content, the footer. */
export function PageShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 pt-5 sm:px-6 lg:px-8">
        <Wordmark />
        <Link
          href="/"
          className="inline-flex h-11 items-center rounded-full border border-line bg-surface px-4 text-sm font-medium text-ink shadow-card transition hover:border-accent hover:text-accent-strong"
        >
          Back to the dashboard
        </Link>
      </header>
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 pb-10 pt-8 sm:px-6">{children}</main>
      <Footer />
    </div>
  );
}
