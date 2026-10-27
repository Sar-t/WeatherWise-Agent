import type { Ref } from "react";

export const ASSISTANT_PANEL_ID = "assistant-panel";

/** The floating "Ask WeatherWise" button. Kept apart from the panel, whose code loads on first open. */
export function AssistantButton({
  ref,
  open,
  panelLoaded,
  onClick,
}: {
  ref?: Ref<HTMLButtonElement>;
  open: boolean;
  panelLoaded: boolean;
  onClick: () => void;
}) {
  return (
    <button
      ref={ref}
      type="button"
      onClick={onClick}
      aria-expanded={open}
      aria-controls={panelLoaded ? ASSISTANT_PANEL_ID : undefined}
      className={`inline-flex h-12 items-center gap-2 rounded-full bg-accent px-5 text-[0.95rem] font-semibold text-white shadow-raised transition hover:bg-accent-strong ${open ? "invisible" : ""}`}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
           strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M20 12a7.5 7.5 0 0 1-11 6.6L4.5 20l1.4-4A7.5 7.5 0 1 1 20 12Z" />
      </svg>
      Ask WeatherWise
    </button>
  );
}
