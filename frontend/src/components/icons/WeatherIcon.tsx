/** Our own weather icons for the API's icon keys: 24px grid, one stroke weight, rounded caps,
 * currentColor. Optional gentle motion (drifting clouds, falling drops), off under
 * prefers-reduced-motion (see globals.css). */

import { useId, type ReactNode } from "react";

export const ICON_KEYS = [
  "clear-day",
  "clear-night",
  "partly-cloudy",
  "partly-cloudy-night",
  "cloudy",
  "rain",
  "heavy-rain",
  "drizzle",
  "thunderstorm",
  "fog",
  "haze",
  "snow",
  "wind",
] as const;
export type IconKey = (typeof ICON_KEYS)[number];

export const ICON_LABELS: Record<IconKey, string> = {
  "clear-day": "Clear",
  "clear-night": "Clear night",
  "partly-cloudy": "Partly cloudy",
  "partly-cloudy-night": "Partly cloudy night",
  cloudy: "Cloudy",
  rain: "Rain",
  "heavy-rain": "Heavy rain",
  drizzle: "Drizzle",
  thunderstorm: "Thunderstorm",
  fog: "Fog",
  haze: "Haze",
  snow: "Snow",
  wind: "Windy",
};

// A cloud spanning roughly x 4-21, y 7-18.
const CLOUD = "M6.5 18h11a3.5 3.5 0 0 0 .4-6.98 5.5 5.5 0 0 0-10.6-1.2A4 4 0 0 0 6.5 18Z";
const MOON = "M19.5 14.5A7.5 7.5 0 1 1 9.5 4.5a6 6 0 0 0 10 10Z";

function sunRays(cx: number, cy: number, inner: number, outer: number): ReactNode {
  return Array.from({ length: 8 }, (_, i) => {
    const a = (i * Math.PI) / 4;
    const x1 = cx + Math.cos(a) * inner;
    const y1 = cy + Math.sin(a) * inner;
    const x2 = cx + Math.cos(a) * outer;
    const y2 = cy + Math.sin(a) * outer;
    return <line key={i} x1={x1.toFixed(2)} y1={y1.toFixed(2)} x2={x2.toFixed(2)} y2={y2.toFixed(2)} />;
  });
}

function Cloud({ transform }: { transform?: string }) {
  return (
    <g className="ww-drift">
      <path d={CLOUD} transform={transform} vectorEffect="non-scaling-stroke" />
    </g>
  );
}

/** A cloud with something behind it (sun/moon), masked so strokes never cross. */
function Behind({ children, cloudTransform }: { children: ReactNode; cloudTransform: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <>
      <mask id={`m${id}`} maskUnits="userSpaceOnUse" x="0" y="0" width="24" height="24">
        <rect width="24" height="24" fill="white" />
        <path d={CLOUD} transform={cloudTransform} fill="black" stroke="black" strokeWidth="3.4"
              vectorEffect="non-scaling-stroke" />
      </mask>
      <g mask={`url(#m${id})`}>{children}</g>
      <Cloud transform={cloudTransform} />
    </>
  );
}

const RAISED = "translate(0 -3)"; // clouds that have rain, snow or lightning under them

function glyph(name: IconKey): ReactNode {
  switch (name) {
    case "clear-day":
      return (
        <>
          <circle cx="12" cy="12" r="4" />
          <g className="ww-glow">{sunRays(12, 12, 6.5, 8.6)}</g>
        </>
      );
    case "clear-night":
      return <path d={MOON} />;
    case "partly-cloudy":
      return (
        <Behind cloudTransform="translate(2.4 2.2) scale(0.86)">
          <circle cx="9" cy="9" r="3.2" />
          <g className="ww-glow">{sunRays(9, 9, 5, 6.6)}</g>
        </Behind>
      );
    case "partly-cloudy-night":
      return (
        <Behind cloudTransform="translate(2.4 2.2) scale(0.86)">
          <path d={MOON} transform="translate(-1.6 -1.8) scale(0.72)" vectorEffect="non-scaling-stroke" />
        </Behind>
      );
    case "cloudy":
      return (
        <>
          <path d={CLOUD} transform="translate(5.5 -3.2) scale(0.7)" vectorEffect="non-scaling-stroke" opacity="0.55" />
          <Cloud transform="translate(-0.5 1)" />
        </>
      );
    case "rain":
      return (
        <>
          <Cloud transform={RAISED} />
          <line className="ww-fall" x1="8.5" y1="18" x2="7.6" y2="20.4" />
          <line className="ww-fall-2" x1="12.5" y1="18" x2="11.6" y2="20.4" />
          <line className="ww-fall-3" x1="16.5" y1="18" x2="15.6" y2="20.4" />
        </>
      );
    case "heavy-rain":
      return (
        <>
          <Cloud transform={RAISED} />
          <line className="ww-fall" x1="7.5" y1="17.6" x2="6.2" y2="21.2" />
          <line className="ww-fall-2" x1="10.5" y1="17.6" x2="9.2" y2="21.2" />
          <line className="ww-fall-3" x1="13.5" y1="17.6" x2="12.2" y2="21.2" />
          <line className="ww-fall" x1="16.5" y1="17.6" x2="15.2" y2="21.2" />
        </>
      );
    case "drizzle":
      return (
        <>
          <Cloud transform={RAISED} />
          <line className="ww-fall" x1="8.6" y1="18.4" x2="8.3" y2="19.2" />
          <line className="ww-fall-2" x1="12.6" y1="18.4" x2="12.3" y2="19.2" />
          <line className="ww-fall-3" x1="16.6" y1="18.4" x2="16.3" y2="19.2" />
          <line className="ww-fall-2" x1="10.4" y1="21" x2="10.1" y2="21.8" />
          <line className="ww-fall" x1="14.4" y1="21" x2="14.1" y2="21.8" />
        </>
      );
    case "thunderstorm":
      return (
        <>
          <Cloud transform={RAISED} />
          <polyline points="12.6 15.6 10.4 19.2 13.2 19.2 11.2 22.6" />
        </>
      );
    case "snow":
      return (
        <>
          <Cloud transform={RAISED} />
          {[
            [8.5, 19.4],
            [12.5, 21],
            [16.5, 19.4],
          ].map(([x, y], i) => (
            <circle key={i} className={["ww-fall", "ww-fall-2", "ww-fall-3"][i]} cx={x} cy={y} r="1.1"
                    fill="currentColor" stroke="none" />
          ))}
        </>
      );
    case "fog":
      return (
        <g className="ww-drift">
          <line x1="4" y1="8" x2="17" y2="8" />
          <line x1="7" y1="12" x2="20" y2="12" />
          <line x1="4" y1="16" x2="15" y2="16" />
          <line x1="9" y1="20" x2="19" y2="20" />
        </g>
      );
    case "haze":
      return (
        <>
          <path d="M7.5 12a4.5 4.5 0 0 1 9 0" />
          <g className="ww-glow">
            <line x1="12" y1="3.6" x2="12" y2="5" />
            <line x1="5.6" y1="6.2" x2="6.6" y2="7.2" />
            <line x1="18.4" y1="6.2" x2="17.4" y2="7.2" />
          </g>
          <g className="ww-drift">
            <line x1="3.5" y1="15" x2="20.5" y2="15" />
            <line x1="6" y1="18.2" x2="18" y2="18.2" />
            <line x1="8.5" y1="21.4" x2="15.5" y2="21.4" />
          </g>
        </>
      );
    case "wind":
      return (
        <g className="ww-drift">
          <path d="M3 9h10.5a2.5 2.5 0 1 0-2.5-2.5" />
          <path d="M3 13h15a2.5 2.5 0 1 1-2.5 2.5" />
          <path d="M3 17h7" />
        </g>
      );
  }
}

type Props = {
  name: string;
  size?: number;
  className?: string;
  /** Accessible name; omit for decorative icons (aria-hidden). */
  title?: string;
  animate?: boolean;
  strokeWidth?: number;
};

export function WeatherIcon({ name, size = 24, className, title, animate = false, strokeWidth = 1.6 }: Props) {
  const key = (ICON_KEYS as readonly string[]).includes(name) ? (name as IconKey) : "cloudy";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`${animate ? "ww-animate " : ""}${className ?? ""}`}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      overflow="visible"
    >
      {glyph(key)}
    </svg>
  );
}
