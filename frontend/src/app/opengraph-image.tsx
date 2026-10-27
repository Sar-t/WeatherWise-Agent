import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { ImageResponse } from "next/og";

// The link-preview image, rendered on request in the dashboard's style: the day sky, the sun
// mark, the display font. No weather numbers: it's shared long after any forecast would be stale.
export const alt = "WeatherWise: weather, air quality and the best time to step out";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const FONTS = join(process.cwd(), "node_modules", "@fontsource");
const font = (pkg: string, file: string) => readFile(join(FONTS, pkg, "files", file));

export default async function Image() {
  const [fraunces, jakarta] = await Promise.all([
    font("fraunces", "fraunces-latin-500-normal.woff"),
    font("plus-jakarta-sans", "plus-jakarta-sans-latin-500-normal.woff"),
  ]);
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: "72px 80px",
          backgroundImage: "linear-gradient(165deg, #cfe6f5 0%, #e3f0f8 50%, #f6f5f2 100%)",
          color: "#1c1917",
          fontFamily: "Jakarta",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="#2e6e8e" strokeWidth="1.7" strokeLinecap="round">
            <path d="M6.5 15.5a5.5 5.5 0 0 1 11 0" />
            <line x1="3" y1="15.5" x2="21" y2="15.5" />
            <line x1="7" y1="19" x2="17" y2="19" />
            <line x1="12" y1="4.5" x2="12" y2="6.5" />
            <line x1="5.2" y1="7.8" x2="6.6" y2="9.2" />
            <line x1="18.8" y1="7.8" x2="17.4" y2="9.2" />
          </svg>
          <div style={{ fontFamily: "Fraunces", fontSize: 44 }}>WeatherWise</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          <div style={{ fontFamily: "Fraunces", fontSize: 76, lineHeight: 1.08, letterSpacing: -1.5, maxWidth: 900 }}>
            Weather, air quality and the best time to step out.
          </div>
          <div style={{ fontSize: 30, color: "#44403c" }}>
            Hourly and 7-day forecasts · CPCB air quality · an assistant for your plans
          </div>
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        { name: "Fraunces", data: fraunces, weight: 500, style: "normal" },
        { name: "Jakarta", data: jakarta, weight: 500, style: "normal" },
      ],
    },
  );
}
