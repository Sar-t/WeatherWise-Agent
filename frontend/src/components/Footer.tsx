import Link from "next/link";
import type { ReactNode } from "react";

/**
 * Data and guidance credits, on every page. The wording follows each provider's terms:
 * OpenWeather recommends "Weather data © OpenWeather" on the pages showing its data; Geoapify's free
 * plan requires its attribution ("Powered by Geoapify") and OpenStreetMap's; OpenStreetMap asks for
 * a credit linking to its copyright page, which states the ODbL licence.
 */
export function Footer() {
  return (
    <footer className="mx-auto w-full max-w-6xl px-4 pb-(--page-end-room) pt-4 text-sm text-muted sm:px-6 lg:px-8">
      <div className="border-t border-line pt-5">
        <p className="leading-relaxed">
          Weather and air quality data © <Out href="https://openweathermap.org/">OpenWeather</Out>.{" "}
          Geocoding powered by <Out href="https://www.geoapify.com/">Geoapify</Out>, map data ©{" "}
          <Out href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</Out> (ODbL).{" "}
          Health guidance: <Out href="https://cpcb.nic.in/">CPCB</Out>, <Out href="https://mohfw.gov.in/">MoHFW</Out>,{" "}
          <Out href="https://www.who.int/">WHO</Out>, <Out href="https://ndma.gov.in/">NDMA</Out>.
        </p>
        <p className="mt-2">
          Not medical advice.{" "}
          <Link href="/about" className="font-medium text-accent-strong underline decoration-accent/30 underline-offset-2 hover:decoration-accent">
            About WeatherWise and privacy
          </Link>
        </p>
      </div>
    </footer>
  );
}

function Out({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer"
       className="text-ink-soft underline decoration-line underline-offset-2 hover:text-accent-strong hover:decoration-accent">
      {children}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}
