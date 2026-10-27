export type HourlyPoint = { hour: string; temp: number; rain: number };

/**
 * The hourly strip's temperature line and rain-chance bars, as plain SVG: one point per column, centred
 * under its hour. Decorative (the columns and the summary carry the numbers), and small enough not to
 * need a chart library at the top of the page.
 */
export default function HourlyChart({
  data,
  width,
  height,
  domain,
  rain,
}: {
  data: HourlyPoint[];
  width: number;
  height: number;
  domain: number[];
  rain: boolean;
}) {
  const col = width / data.length;
  const top = 4;
  const [lo, hi] = domain;
  const y = (temp: number) => top + (1 - (temp - lo) / (hi - lo || 1)) * (height - top);
  const points = data.map((d, i) => [col * i + col / 2, y(d.temp)] as const);
  // A gentle curve through the points (Catmull-Rom as cubic Béziers).
  const path = points
    .map(([x, py], i) => {
      if (i === 0) return `M ${x.toFixed(1)} ${py.toFixed(1)}`;
      const [x0, y0] = points[i - 1];
      const [xm, ym] = points[i - 2] ?? points[i - 1];
      const [x2, y2] = points[i + 1] ?? [x, py];
      const c1 = [x0 + (x - xm) / 6, y0 + (py - ym) / 6];
      const c2 = [x - (x2 - x0) / 6, py - (y2 - y0) / 6];
      return `C ${c1[0].toFixed(1)} ${c1[1].toFixed(1)}, ${c2[0].toFixed(1)} ${c2[1].toFixed(1)}, ${x.toFixed(1)} ${py.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden focusable="false">
      {rain &&
        data.map((d, i) =>
          d.rain > 0 ? (
            <rect key={d.hour} x={col * i + (col - 20) / 2} y={height - (d.rain / 100) * (height - top)} width={20}
                  height={(d.rain / 100) * (height - top)} rx={3} fill="#b9d3e0" />
          ) : null,
        )}
      <path d={path} fill="none" stroke="var(--color-accent)" strokeWidth={2} strokeLinecap="round" />
    </svg>
  );
}
