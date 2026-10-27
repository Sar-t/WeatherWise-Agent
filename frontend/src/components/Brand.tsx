import Link from "next/link";

/** The WeatherWise sun mark: a sun half-risen over a horizon. Also the source of the app icons. */
export function Mark({ size = 26, className = "text-accent" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
         strokeLinecap="round" aria-hidden className={className}>
      <path d="M6.5 15.5a5.5 5.5 0 0 1 11 0" />
      <line x1="3" y1="15.5" x2="21" y2="15.5" />
      <line x1="7" y1="19" x2="17" y2="19" />
      <line x1="12" y1="4.5" x2="12" y2="6.5" />
      <line x1="5.2" y1="7.8" x2="6.6" y2="9.2" />
      <line x1="18.8" y1="7.8" x2="17.4" y2="9.2" />
    </svg>
  );
}

/** The wordmark, linking home. */
export function Wordmark() {
  return (
    <Link href="/" className="flex items-center gap-2 rounded-lg text-ink" aria-label="WeatherWise home">
      <Mark />
      <span className="font-display text-[1.35rem] font-semibold tracking-tight">WeatherWise</span>
    </Link>
  );
}
