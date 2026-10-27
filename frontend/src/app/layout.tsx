import type { Metadata, Viewport } from "next";
import { API_BASE_URL } from "@/lib/api";
import { fontClasses } from "./fonts";
import "./globals.css";
import { Providers } from "./providers";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";
const DESCRIPTION =
  "Weather, air quality and the best time to step out, for India and beyond. Hourly and 7-day forecasts, CPCB air quality and an assistant for your plans.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL), // absolute URLs for the share image and canonical links
  title: { default: "WeatherWise: weather, air quality and the best time to step out", template: "%s · WeatherWise" },
  description: DESCRIPTION,
  applicationName: "WeatherWise",
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    siteName: "WeatherWise",
    locale: "en_IN",
    title: "WeatherWise",
    description: DESCRIPTION,
  },
  twitter: { card: "summary_large_image", title: "WeatherWise", description: DESCRIPTION },
  appleWebApp: { title: "WeatherWise", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  themeColor: "#f6f5f2",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${fontClasses} h-full antialiased`}>
      <head>
        {/* The dashboard's first request goes to the API: open that connection while the page loads. */}
        <link rel="preconnect" href={API_BASE_URL} crossOrigin="anonymous" />
      </head>
      <body className="min-h-full">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
