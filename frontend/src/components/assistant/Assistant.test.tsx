/// <reference types="vite/client" />
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatResponse, Dashboard } from "@/lib/api";
import realConversation from "@/test/fixtures/conversation-delhi-run.json";
import realConversations from "@/test/fixtures/conversations.json";
import delhi from "@/test/fixtures/dashboard-delhi.json";
import realStream from "@/test/fixtures/chat-stream-delhi-run.sse?raw";
import { controlled, sse } from "@/test/streams";
import { Assistant } from "./Assistant";

// The canonical reply recorded from the real backend (a run in Delhi tomorrow evening).
const REAL_MESSAGE: ChatResponse = JSON.parse(/event: message\r?\ndata: (.*)/.exec(realStream)![1]);
const data = delhi as Dashboard;

function Harness({ dashboard = data }: { dashboard?: Dashboard }) {
  const [open, setOpen] = useState(false);
  return <Assistant data={dashboard} open={open} onOpen={() => setOpen(true)} onClose={() => setOpen(false)} />;
}

type Handler = (init: RequestInit) => Response | Promise<Response>;
let routes: Record<string, Handler>;
const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
  const url = new URL(String(input));
  const key = `${init.method ?? "GET"} ${url.pathname}`;
  const handler = routes[key];
  if (!handler) throw new Error(`unexpected request ${key}`);
  return handler(init);
});

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

const NEW_CONVERSATION = { id: "c-new", title: "", created_at: "2026-10-25T16:30:00Z", updated_at: "2026-10-25T16:30:00Z" };

/** Serve the chat stream from a body the test controls; abort errors it like a real fetch. */
function streamRoute() {
  const stream = controlled();
  const bodies: Record<string, unknown>[] = [];
  routes["POST /api/v1/chat/stream"] = (init) => {
    bodies.push(JSON.parse(String(init.body)));
    init.signal?.addEventListener("abort", () => stream.fail(new DOMException("Aborted", "AbortError")));
    return new Response(stream.body, { headers: { "Content-Type": "text/event-stream" } });
  };
  return { ...stream, bodies };
}

/** The message list: the live region repeats the final reply, so look here for what's shown. */
const conversation = () => within(screen.getByRole("list", { name: "Conversation" }));

async function openPanel() {
  const user = userEvent.setup();
  render(<Harness />);
  await user.click(screen.getByRole("button", { name: "Ask WeatherWise" }));
  return user;
}

async function ask(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByRole("textbox", { name: "Your question" }), text);
  await user.keyboard("{Enter}");
}

beforeEach(() => {
  routes = { "POST /api/v1/conversations": () => json(NEW_CONVERSATION, 201) };
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("opening and closing", () => {
  it("moves focus into the panel, closes on Esc and returns focus to the button", async () => {
    const user = await openPanel();
    const dialog = screen.getByRole("dialog", { name: "Ask WeatherWise" });
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveFocus();
    expect(within(dialog).getByText("Asking about New Delhi")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Ask WeatherWise" })).toHaveFocus();
    expect(dialog).toHaveAttribute("inert");
  });

  it("traps focus while it's a modal sheet (phones)", async () => {
    const user = await openPanel();
    const dialog = screen.getByRole("dialog", { name: "Ask WeatherWise" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    const buttons = within(dialog).getAllByRole("button");
    buttons[0].focus(); // the first focusable control
    await user.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    within(dialog).getByRole("textbox").focus();
    for (let i = 0; i < 6; i++) await user.tab();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
  });
});

describe("asking", () => {
  it("offers chips from the dashboard and sends the dashboard's place with the question", async () => {
    const stream = streamRoute();
    const user = await openPanel();
    const chips = within(screen.getByRole("list", { name: "Suggested questions" })).getAllByRole("button");
    expect(chips.map((c) => c.textContent)).toEqual(["Best time for a run tomorrow?", "What should I wear today?"]);

    await user.click(chips[0]);
    await waitFor(() => expect(stream.bodies).toHaveLength(1));
    expect(stream.bodies[0]).toEqual({
      message: "Best time for a run tomorrow?",
      conversation_id: "c-new",
      location: { lat: 28.61, lon: 77.21 },
      timezone: "Asia/Kolkata",
    });
    expect(screen.queryByRole("list", { name: "Suggested questions" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem("ww.conversationId")).toBe("c-new");
  });

  it("streams status and tokens, then shows the canonical reply with its assessment, air and sources", async () => {
    const stream = streamRoute();
    const user = await openPanel();
    await ask(user, "Can I go for a run in Delhi tomorrow evening?");

    act(() => stream.push(sse("status", { text: "checking air quality" })));
    expect(await screen.findByText("Checking air quality…")).toBeInTheDocument();
    act(() => stream.push(sse("token", { text: "Running is " }) + sse("token", { text: "a good idea" })));
    expect(await screen.findByText("Running is a good idea")).toBeInTheDocument();
    // Only the final reply is announced, not each token.
    expect(screen.getByRole("status")).toHaveTextContent("");

    act(() => {
      stream.push(sse("message", REAL_MESSAGE));
      stream.end();
    });
    expect(await conversation().findByText(REAL_MESSAGE.text.slice(0, 40), { exact: false })).toBeInTheDocument();
    expect(screen.queryByText("Running is a good idea")).not.toBeInTheDocument();
    expect(screen.queryByText("Checking air quality…")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(REAL_MESSAGE.text.slice(0, 30));

    const card = screen.getByRole("region", { name: "Running in New Delhi" });
    expect(card).toHaveTextContent("Good");
    expect(card).toHaveTextContent("100 of 100");
    expect(card).toHaveTextContent(/20:30 – 21:30/);
    // The air chip says what it shows: the window's worst hour, then the best window's own reading.
    const worst = screen.getByText("Worst in window:").parentElement!;
    expect(worst).toHaveTextContent("Worst in window: NAQI 130, Moderately Polluted (17:30)");
    expect(screen.getByText("In your best window:").parentElement).toHaveTextContent(
      "In your best window: NAQI 99, Satisfactory",
    );

    const source = screen.getByRole("link", { name: /NAQI Moderately Polluted \(101-200\)/ });
    expect(source).toHaveAttribute("href", REAL_MESSAGE.sources![0].url);
    expect(source).toHaveAttribute("target", "_blank");
    expect(source).toHaveAttribute("rel", "noopener noreferrer");
    expect(source).toHaveTextContent("CPCB (Central Pollution Control Board)");
  });

  it("shows HTML in a reply as text", async () => {
    const stream = streamRoute();
    const user = await openPanel();
    await ask(user, "hi");
    const html = '<b>bold</b> <img src="x" onerror="alert(1)"> <a href="https://example.com">link</a>';
    act(() => {
      stream.push(sse("message", { ...REAL_MESSAGE, text: html, assessments: [], air_quality: [], sources: [] }));
      stream.end();
    });
    const dialog = screen.getByRole("dialog");
    expect(await conversation().findByText(html)).toBeInTheDocument();
    expect(dialog.querySelector("b, img, a[href='https://example.com']")).toBeNull();
  });

  it("stops a reply and keeps the partial text, marked Stopped", async () => {
    const stream = streamRoute();
    const user = await openPanel();
    await ask(user, "Will it rain?");
    act(() => stream.push(sse("token", { text: "Probably not" })));
    await screen.findByText("Probably not");

    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(await screen.findByText("Stopped")).toBeInTheDocument();
    expect(screen.getByText("Probably not")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  });
});

describe("errors and limits", () => {
  it("shows an error event with Retry, and retrying reuses the question", async () => {
    const first = streamRoute();
    const user = await openPanel();
    await ask(user, "Will it rain in Pune?");
    act(() => {
      first.push(sse("error", { code: "agent_error", message: "The weather service is busy. Please try again." }));
      first.end();
    });
    expect(await conversation().findByText("The weather service is busy. Please try again.")).toBeInTheDocument();

    const second = streamRoute();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    act(() => {
      second.push(sse("message", REAL_MESSAGE));
      second.end();
    });
    await screen.findByRole("region", { name: "Running in New Delhi" });
    expect(screen.getAllByText("Will it rain in Pune?")).toHaveLength(1); // one bubble, not two
    expect(second.bodies[0].message).toBe("Will it rain in Pune?");
  });

  it("offers Retry after a network failure", async () => {
    routes["POST /api/v1/chat/stream"] = () => Promise.reject(new TypeError("Failed to fetch"));
    const user = await openPanel();
    await ask(user, "hello");
    expect(await conversation().findByText(/Can't reach WeatherWise right now/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("counts down the per-minute limit before Send works again", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    routes["POST /api/v1/chat/stream"] = () =>
      json({ error: { code: "rate_limited", message: "Too fast." } }, 429, { "Retry-After": "3" });
    const user = await openPanel();
    await ask(user, "hello");
    expect(await conversation().findByText("You're sending messages too quickly.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry in 3 s" })).toBeDisabled();

    await user.type(screen.getByRole("textbox"), "again");
    expect(screen.getByRole("button", { name: "Wait 3 s" })).toBeDisabled();
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByRole("button", { name: "Wait 2 s" })).toBeDisabled();
    act(() => vi.advanceTimersByTime(2500));
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("explains the daily cap, without a Retry", async () => {
    const message = "You've reached today's limit of 50 messages. It resets at midnight (India time).";
    routes["POST /api/v1/chat/stream"] = () =>
      json({ error: { code: "daily_limit", message } }, 429, { "Retry-After": "7200" });
    const user = await openPanel();
    await ask(user, "hello");
    expect(await conversation().findByText(message)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry/ })).not.toBeInTheDocument();
  });

  it("counts characters near the 500 limit", async () => {
    const user = await openPanel();
    const box = screen.getByRole("textbox", { name: "Your question" });
    await user.click(box);
    await user.paste("a".repeat(399));
    expect(screen.queryByText("399/500")).not.toBeInTheDocument();
    await user.paste("bb");
    expect(screen.getByText("401/500")).toBeInTheDocument();
    expect(box).toHaveAttribute("maxLength", "500");
  });
});

describe("conversations", () => {
  const id = realConversation.conversation.id;

  it("restores the saved conversation after a refresh", async () => {
    window.localStorage.setItem("ww.conversationId", id);
    routes[`GET /api/v1/conversations/${id}`] = () => json(realConversation);
    await openPanel();
    const [question, answer] = realConversation.messages;
    expect(await screen.findByText(question.text)).toBeInTheDocument();
    expect(screen.getByText(answer.text.slice(0, 40), { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Running in New Delhi" })).toBeInTheDocument();
  });

  it("starts fresh with New chat", async () => {
    window.localStorage.setItem("ww.conversationId", id);
    routes[`GET /api/v1/conversations/${id}`] = () => json(realConversation);
    const user = await openPanel();
    await screen.findByText(realConversation.messages[0].text);

    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByText(realConversation.messages[0].text)).not.toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Suggested questions" })).toBeInTheDocument();
    expect(window.localStorage.getItem("ww.conversationId")).toBeNull();
  });

  it("lists earlier conversations without the empty ones, and reopens one", async () => {
    routes["GET /api/v1/conversations"] = () => json(realConversations);
    routes[`GET /api/v1/conversations/${id}`] = () => json(realConversation);
    const user = await openPanel();
    await user.click(screen.getByRole("button", { name: "Conversation history" }));

    const menu = await screen.findByRole("menu", { name: "Conversation history" });
    const items = within(menu).getAllByRole("menuitem");
    expect(realConversations.some((c) => !c.title)).toBe(true); // the real list has an empty one
    expect(items).toHaveLength(realConversations.filter((c) => c.title).length);

    await user.click(within(menu).getByRole("menuitem", { name: (name) => name.startsWith(realConversation.conversation.title) }));
    expect(await screen.findByText(realConversation.messages[0].text)).toBeInTheDocument();
    expect(window.localStorage.getItem("ww.conversationId")).toBe(id);
  });
});
