"use client";

import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export type AirBar = { hour: string; time: string; value: number; category: string; colour: string };

/** The air quality card's 24-hour bars. Loaded on its own (Recharts is large); decorative, so no
 * keyboard layer: the card's text summary carries the numbers. */
export default function AirChart({ data, scale }: { data: AirBar[]; scale: "naqi" | "owm" }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }} barCategoryGap={2} accessibilityLayer={false}>
        <XAxis dataKey="hour" interval={5} tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: "var(--color-muted)" }} />
        <YAxis hide domain={[0, "dataMax"]} />
        <Tooltip
          cursor={{ fill: "var(--color-line-soft)" }}
          contentStyle={{ borderRadius: 12, border: "1px solid var(--color-line)", fontSize: 12 }}
          labelFormatter={(_, payload) => payload?.[0]?.payload?.time ?? ""}
          formatter={(value, _name, item) => [`${value}${scale === "owm" ? "/5" : ""} · ${item.payload.category}`,
            scale === "naqi" ? "NAQI" : "Level"]}
        />
        <Bar dataKey="value" radius={[3, 3, 0, 0]} isAnimationActive={false}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.colour} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
