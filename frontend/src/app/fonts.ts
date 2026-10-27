import { Fraunces, Plus_Jakarta_Sans } from "next/font/google";

// Self-hosted by next/font at build time (no request to Google), with size-adjusted fallbacks so text
// doesn't shift when the fonts arrive. Shared by the root layout and global-error, which replaces it.
export const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  axes: ["SOFT", "opsz"],
  display: "swap",
});

export const jakarta = Plus_Jakarta_Sans({
  variable: "--font-jakarta",
  subsets: ["latin"],
  display: "swap",
});

export const fontClasses = `${fraunces.variable} ${jakarta.variable}`;
