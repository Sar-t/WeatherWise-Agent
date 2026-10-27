"use client"; // error boundaries are Client Components

import { ErrorPage } from "@/components/ErrorPage";
import { fontClasses } from "./fonts";
import "./globals.css";

/** Errors in the root layout itself: this replaces the layout, so it brings its own document, styles and fonts. */
export default function GlobalError({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <html lang="en" className={`${fontClasses} h-full antialiased`}>
      <body className="min-h-full">
        <title>Something went wrong · WeatherWise</title>
        <ErrorPage error={error} retry={retry} />
      </body>
    </html>
  );
}
