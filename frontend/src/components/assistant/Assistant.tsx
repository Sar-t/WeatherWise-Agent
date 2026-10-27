"use client";

import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore, type KeyboardEvent, type RefObject } from "react";
import { ApiError, type ChatResponse, type ConversationDetail, type ConversationOut, type Dashboard } from "@/lib/api";
import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  loadConversationId,
  saveConversationId,
  streamChat,
} from "@/lib/chat";
import { suggestions } from "@/lib/suggestions";
import { UiIcon } from "../icons/UiIcon";
import { ASSISTANT_PANEL_ID } from "./AssistantButton";
import { ReplyExtras, ReplyText } from "./AssistantMessage";

export const MAX_CHARS = 500;
const COUNTER_FROM = 400;
const DESKTOP = "(min-width: 1280px)"; // side panel beside the dashboard; below this, a full-height sheet
const NEAR_BOTTOM = 48; // px: closer than this to the end of the list counts as following it
const REPLY_GAP = 12; // px above a finished reply when it's brought to the top

type UserTurn = { id: string; role: "user"; text: string };
type AssistantTurn = {
  id: string;
  role: "assistant";
  replyTo: string; // the user turn it answers
  state: "streaming" | "done" | "stopped" | "error";
  text: string;
  status?: string;
  response?: ChatResponse;
  error?: { message: string; retry: boolean };
};
type Turn = UserTurn | AssistantTurn;

/** The backend's step names ("checking air quality") as a quiet progress line. */
export function statusLine(step: string): string {
  const text = step.trim();
  return text ? `${text[0].toUpperCase()}${text.slice(1)}…` : "Working…";
}

let counter = 0;
const newId = () => `t${++counter}`;

function subscribeDesktop(onChange: () => void) {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const query = window.matchMedia(DESKTOP);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}
const isDesktop = () => typeof window !== "undefined" && !!window.matchMedia?.(DESKTOP).matches;

/** Turns from a stored conversation. */
function fromHistory(detail: ConversationDetail): Turn[] {
  const turns: Turn[] = [];
  let lastUser: string | null = null;
  for (const m of detail.messages) {
    if (m.role === "user") {
      lastUser = newId();
      turns.push({ id: lastUser, role: "user", text: m.text });
    } else if (lastUser) {
      const failed = (m.response?.kind ?? m.kind) === "error";
      turns.push({
        id: newId(),
        role: "assistant",
        replyTo: lastUser,
        state: failed ? "error" : "done",
        text: failed ? "" : m.response?.text ?? m.text,
        response: failed ? undefined : m.response ?? undefined,
        error: failed ? { message: m.response?.text ?? m.text, retry: true } : undefined,
      });
    }
  }
  return turns;
}

type Props = {
  data?: Dashboard;
  open: boolean;
  onClose: () => void;
  /** The "Ask WeatherWise" button, which gets focus back when the panel closes. */
  buttonRef: RefObject<HTMLButtonElement | null>;
};

export function Assistant({ data, open, onClose, buttonRef }: Props) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true); // false once the user scrolls up to read; sending turns it back on
  const landOnRef = useRef<string | null>(null); // a reply that just finished, to show from its start
  const abortRef = useRef<AbortController | null>(null);
  const conversationRef = useRef<string | null>(null);
  const wasOpen = useRef(false);
  const desktop = useSyncExternalStore(subscribeDesktop, isDesktop, () => false);

  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const [cooldownUntil, setCooldownUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [history, setHistory] = useState<ConversationOut[] | null>(null);

  // Continue the last conversation after a refresh.
  useEffect(() => {
    const id = loadConversationId();
    if (!id) return;
    conversationRef.current = id;
    let cancelled = false;
    getConversation(id)
      .then((detail) => !cancelled && setTurns(fromHistory(detail)))
      .catch(() => {
        if (cancelled) return;
        conversationRef.current = null; // deleted or not ours any more: start fresh
        saveConversationId(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Focus into the panel on open, back to the button on close.
  useEffect(() => {
    if (open) inputRef.current?.focus();
    else if (wasOpen.current) buttonRef.current?.focus();
    wasOpen.current = open;
  }, [open, buttonRef]);

  // While streaming, follow the newest text; when a reply finishes, bring its start to the top. Both
  // only while the user is following: someone who scrolled up to read stays where they are.
  useEffect(() => {
    const list = listRef.current;
    const landOn = landOnRef.current;
    landOnRef.current = null;
    if (!list || !followRef.current) return;
    const reply = landOn ? list.querySelector<HTMLElement>(`[data-turn="${landOn}"]`) : null;
    list.scrollTo?.({ top: reply ? reply.offsetTop - REPLY_GAP : list.scrollHeight });
  }, [turns]);

  // The per-minute limit's countdown.
  useEffect(() => {
    if (cooldownUntil == null) return;
    const id = window.setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (t >= cooldownUntil) setCooldownUntil(null);
    }, 250);
    return () => window.clearInterval(id);
  }, [cooldownUntil]);
  const cooldown = cooldownUntil != null ? Math.max(0, Math.ceil((cooldownUntil - now) / 1000)) : 0;

  const patch = useCallback((id: string, change: Partial<AssistantTurn> | ((t: AssistantTurn) => Partial<AssistantTurn>)) => {
    setTurns((ts) =>
      ts.map((t) => (t.id === id && t.role === "assistant" ? { ...t, ...(typeof change === "function" ? change(t) : change) } : t)),
    );
  }, []);

  /** Ask `text` for the user turn `userId`, replacing any earlier reply to it (a retry). */
  const ask = useCallback(
    async (userId: string, text: string) => {
      const replyId = newId();
      const reply: AssistantTurn = { id: replyId, role: "assistant", replyTo: userId, state: "streaming", text: "" };
      setTurns((ts) =>
        ts.some((t) => t.role === "assistant" && t.replyTo === userId)
          ? ts.map((t) => (t.role === "assistant" && t.replyTo === userId ? reply : t))
          : [...ts, reply],
      );
      setBusy(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        // A new chat gets its ID first, so even a stopped first turn can be reopened after a refresh.
        if (!conversationRef.current) {
          const created = await createConversation();
          conversationRef.current = created.id;
          saveConversationId(created.id);
        }
        await streamChat(
          {
            message: text,
            conversation_id: conversationRef.current,
            location: data ? { lat: data.location.lat, lon: data.location.lon } : null, // already 2 decimals
            timezone: data?.location.timezone ?? null,
          },
          (event) => {
            if (event.type === "status") patch(replyId, { status: statusLine(event.text) });
            else if (event.type === "token") patch(replyId, (t) => ({ text: t.text + event.text }));
            else if (event.type === "message") {
              landOnRef.current = replyId;
              patch(replyId, { state: "done", text: event.response.text, response: event.response, status: undefined });
              setAnnouncement(event.response.text);
            } else {
              patch(replyId, { state: "error", status: undefined, error: { message: event.message, retry: true } });
              setAnnouncement(event.message);
            }
          },
          controller.signal,
        );
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          patch(replyId, { state: "stopped", status: undefined });
        } else {
          let message = "Something went wrong. Please try again.";
          let retry = true;
          if (error instanceof ApiError) {
            message = error.message;
            if (error.code === "daily_limit") retry = false;
            else if (error.status === 429) {
              message = "You're sending messages too quickly.";
              setNow(Date.now());
              setCooldownUntil(Date.now() + (error.retryAfter ?? 30) * 1000);
            } else if (error.status === 404) {
              conversationRef.current = null; // the conversation is gone; a retry starts a new one
              saveConversationId(null);
            } else if (error.status === 400) retry = false;
          }
          patch(replyId, { state: "error", status: undefined, error: { message, retry } });
          setAnnouncement(message);
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setBusy(false);
      }
    },
    [data, patch],
  );

  function submit(text: string) {
    const message = text.trim();
    if (!message || busy || cooldown > 0 || message.length > MAX_CHARS) return;
    const id = newId();
    followRef.current = true;
    setTurns((ts) => [...ts, { id, role: "user", text: message }]);
    setDraft("");
    void ask(id, message);
  }

  function retry(reply: AssistantTurn) {
    const user = turns.find((t) => t.id === reply.replyTo);
    if (user && !busy) {
      followRef.current = true;
      void ask(user.id, user.text);
    }
  }

  function reset(id: string | null, next: Turn[]) {
    abortRef.current?.abort();
    conversationRef.current = id;
    saveConversationId(id);
    followRef.current = true;
    setTurns(next);
    setAnnouncement("");
    setHistory(null);
  }

  async function toggleHistory() {
    if (history) return setHistory(null);
    try {
      // Conversations get their title from their first stored message: no title, nothing to reopen.
      setHistory((await listConversations()).filter((c) => c.title.trim()));
    } catch {
      setHistory([]);
    }
  }

  async function reopen(id: string) {
    try {
      reset(id, fromHistory(await getConversation(id)));
    } catch {
      setHistory(null);
    }
    inputRef.current?.focus();
  }

  async function remove(id: string) {
    try {
      await deleteConversation(id);
    } catch {
      return;
    }
    setHistory((h) => h?.filter((c) => c.id !== id) ?? null);
    if (conversationRef.current === id) reset(null, []);
  }

  function onPanelKeyDown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.stopPropagation();
      if (history) setHistory(null);
      else onClose();
      return;
    }
    if (event.key !== "Tab" || desktop) return;
    // A modal sheet on phones: keep focus inside it.
    const focusable = panelRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  const chips = data ? suggestions(data) : ["Best time for a run tomorrow?", "What should I wear today?"];
  const place = data?.location.name;
  const remaining = MAX_CHARS - draft.length;

  return (
    <>
      <div
        id={ASSISTANT_PANEL_ID}
        ref={panelRef}
        role="dialog"
        aria-modal={desktop ? undefined : true}
        aria-labelledby={titleId}
        inert={!open}
        onKeyDown={onPanelKeyDown}
        className={`fixed inset-0 z-40 flex flex-col bg-surface transition-transform duration-300 ease-out motion-reduce:transition-none xl:left-auto xl:w-[420px] xl:border-l xl:border-line xl:shadow-raised ${
          open ? "translate-y-0 xl:translate-x-0" : "translate-y-full xl:translate-x-full xl:translate-y-0"
        }`}
      >
        <header className="flex items-center gap-2 border-b border-line-soft px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="font-display text-lg font-semibold tracking-tight">
              Ask WeatherWise
            </h2>
            {place && (
              <p className="mt-0.5 inline-flex max-w-full items-center gap-1 truncate rounded-full bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-accent-strong">
                <UiIcon name="pin" size={12} />
                Asking about {place}
              </p>
            )}
          </div>
          <div className="relative">
            <HeaderButton label="Conversation history" onClick={() => void toggleHistory()} expanded={!!history}>
              <UiIcon name="history" size={18} />
            </HeaderButton>
            {history && (
              <div
                role="menu"
                aria-label="Conversation history"
                className="absolute right-0 top-full z-10 mt-2 w-72 rounded-2xl border border-line bg-surface p-2 shadow-raised"
              >
                {history.length === 0 ? (
                  <p className="px-3 py-2 text-sm text-muted">No earlier conversations.</p>
                ) : (
                  <ul className="flex max-h-72 flex-col overflow-y-auto">
                    {history.map((c) => (
                      <li key={c.id} className="flex items-center gap-1">
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => void reopen(c.id)}
                          className="min-w-0 flex-1 rounded-xl px-3 py-2 text-left hover:bg-canvas"
                        >
                          <span className="block truncate text-sm font-medium text-ink">{c.title}</span>
                          <span className="block text-xs text-muted">
                            {new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short", hourCycle: "h23" }).format(
                              new Date(c.updated_at),
                            )}
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => void remove(c.id)}
                          aria-label={`Delete "${c.title}"`}
                          className="rounded-full p-2 text-muted hover:text-ink"
                        >
                          <UiIcon name="trash" size={16} />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
          <HeaderButton label="New chat" onClick={() => reset(null, [])}>
            <UiIcon name="plus" size={18} />
          </HeaderButton>
          <HeaderButton label="Close assistant" onClick={onClose}>
            <UiIcon name="close" size={18} />
          </HeaderButton>
        </header>

        <div
          ref={listRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM;
          }}
          className="relative flex-1 overflow-y-auto px-4 py-5"
        >
          {turns.length === 0 ? (
            <div>
              <p className="text-[0.95rem] text-ink-soft">
                Ask about the weather, air quality or the best time for something outdoors
                {place ? ` in ${place}, or anywhere else` : ""}.
              </p>
              <ul className="mt-4 flex flex-wrap gap-2" aria-label="Suggested questions">
                {chips.map((chip) => (
                  <li key={chip}>
                    <button
                      type="button"
                      onClick={() => submit(chip)}
                      disabled={busy || cooldown > 0}
                      className="rounded-full border border-line bg-surface px-3.5 py-2 text-sm text-ink transition hover:border-accent hover:text-accent-strong disabled:opacity-50"
                    >
                      {chip}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <ol className="flex flex-col gap-5" aria-label="Conversation">
              {turns.map((t) =>
                t.role === "user" ? (
                  <li key={t.id} data-turn={t.id} className="ml-auto max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent-soft px-4 py-2.5 text-[0.95rem] text-ink">
                    <span className="sr-only">You: </span>
                    {t.text}
                  </li>
                ) : (
                  <li key={t.id} data-turn={t.id} className="text-[0.95rem] leading-relaxed text-ink">
                    <Reply turn={t} cooldown={cooldown} onRetry={() => retry(t)} />
                  </li>
                ),
              )}
            </ol>
          )}
        </div>

        <form
          className="border-t border-line-soft px-4 pb-3 pt-3"
          onSubmit={(e) => {
            e.preventDefault();
            submit(draft);
          }}
        >
          <div className="flex items-end gap-2 rounded-2xl border border-line bg-surface p-2 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/25">
            <label htmlFor="assistant-input" className="sr-only">
              Your question
            </label>
            <textarea
              ref={inputRef}
              id="assistant-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  submit(draft);
                }
              }}
              maxLength={MAX_CHARS}
              rows={2}
              placeholder="Ask about weather, air or plans…"
              aria-describedby="assistant-counter"
              className="max-h-40 min-h-[2.75rem] flex-1 resize-none bg-transparent px-2 py-1.5 text-[0.95rem] text-ink placeholder:text-muted focus:outline-none"
            />
            {busy ? (
              <button
                type="button"
                onClick={() => abortRef.current?.abort()}
                className="h-10 shrink-0 rounded-xl border border-line px-4 text-sm font-semibold text-ink hover:border-accent"
              >
                Stop
              </button>
            ) : (
              <button
                type="submit"
                disabled={!draft.trim() || cooldown > 0}
                className="h-10 shrink-0 rounded-xl bg-accent px-4 text-sm font-semibold text-white hover:bg-accent-strong disabled:opacity-50"
              >
                {cooldown > 0 ? `Wait ${cooldown} s` : "Send"}
              </button>
            )}
          </div>
          <div className="mt-2 flex items-start justify-between gap-3 text-xs text-muted">
            <p>Answers can contain mistakes. Safety advice comes from the cited official sources.</p>
            <p id="assistant-counter" className={`tabular shrink-0 ${remaining < 20 ? "font-semibold text-red-800" : ""}`}>
              {draft.length >= COUNTER_FROM ? `${draft.length}/${MAX_CHARS}` : ""}
            </p>
          </div>
        </form>

        {/* Announces each finished reply once, not every streamed token. */}
        <div className="sr-only" aria-live="polite" role="status">
          {announcement}
        </div>
      </div>
    </>
  );
}

function Reply({ turn, cooldown, onRetry }: { turn: AssistantTurn; cooldown: number; onRetry: () => void }) {
  if (turn.state === "error" && turn.error) {
    return (
      <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-amber-950">
        <p>{turn.error.message}</p>
        {turn.error.retry && (
          <button
            type="button"
            onClick={onRetry}
            disabled={cooldown > 0}
            className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-amber-300 bg-white px-3 py-1 text-sm font-medium hover:border-amber-500 disabled:opacity-60"
          >
            <UiIcon name="refresh" size={14} />
            {cooldown > 0 ? `Retry in ${cooldown} s` : "Retry"}
          </button>
        )}
      </div>
    );
  }
  return (
    <div>
      {turn.status && turn.state === "streaming" && <p className="mb-1 text-sm text-muted">{turn.status}</p>}
      {turn.text ? (
        <ReplyText text={turn.text} />
      ) : (
        turn.state === "streaming" && !turn.status && <p className="text-sm text-muted">Thinking…</p>
      )}
      {turn.state === "stopped" && <p className="mt-1 text-xs font-medium text-muted">Stopped</p>}
      {turn.state === "done" && turn.response && <ReplyExtras response={turn.response} />}
    </div>
  );
}

function HeaderButton({ label, onClick, expanded, children }: { label: string; onClick: () => void; expanded?: boolean; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-expanded={expanded}
      className="grid h-9 w-9 place-items-center rounded-full text-ink-soft hover:bg-canvas hover:text-ink"
    >
      {children}
    </button>
  );
}
