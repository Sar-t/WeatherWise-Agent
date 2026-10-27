"use client";

import { useEffect, useRef, useState, type HTMLAttributes } from "react";

/** Renders its children once its box comes near the viewport, so content further down the page (the
 * charts, the cards below the fold on a phone) doesn't load or render during the first paint. Until
 * then the box keeps its own size, or `reserve` (a CSS height) as a stand-in. */
export function WhenVisible({ children, reserve, style, ...box }: HTMLAttributes<HTMLDivElement> & { reserve?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  // Without IntersectionObserver (old browsers, tests) there's nothing to wait for.
  const [visible, setVisible] = useState(() => typeof IntersectionObserver === "undefined");

  useEffect(() => {
    const el = ref.current;
    if (visible || !el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [visible]);

  return (
    <div ref={ref} style={visible || !reserve ? style : { ...style, minHeight: reserve }} {...box}>
      {visible ? children : null}
    </div>
  );
}
