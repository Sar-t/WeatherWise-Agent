import type { ReactNode } from "react";

export function Card({ children, className = "", label }: { children: ReactNode; className?: string; label?: string }) {
  return (
    <section
      aria-label={label}
      className={`rounded-[var(--radius-card)] border border-line/80 bg-surface p-6 shadow-card ${className}`}
    >
      {children}
    </section>
  );
}

/** A card's heading row: the title, and an optional quiet note on the right. */
export function CardTitle({ children, meta, id }: { children: ReactNode; meta?: ReactNode; id?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <h2 id={id} className="text-[0.95rem] font-semibold text-ink">
        {children}
      </h2>
      {meta && <p className="text-xs text-muted">{meta}</p>}
    </div>
  );
}
