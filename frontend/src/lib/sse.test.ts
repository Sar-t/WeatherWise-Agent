/// <reference types="vite/client" />
import { describe, expect, it, vi } from "vitest";
import realStream from "@/test/fixtures/chat-stream-delhi-run.sse?raw";
import { chunked } from "@/test/streams";
import { streamChat, type StreamEvent } from "./chat";
import { parseSse, type SseEvent } from "./sse";

async function collect(chunks: string[]): Promise<SseEvent[]> {
  const out: SseEvent[] = [];
  for await (const e of parseSse(chunked(chunks))) out.push(e);
  return out;
}

describe("parseSse", () => {
  it("joins an event split across chunks, even mid-field", async () => {
    expect(await collect(["eve", "nt: tok", 'en\ndata: {"te', 'xt": "Hel"}\n', "\n"])).toEqual([
      { event: "token", data: '{"text": "Hel"}' },
    ]);
  });

  it("splits several events in one chunk", async () => {
    const events = await collect(['event: status\ndata: {"text": "a"}\n\nevent: token\ndata: {"text": "b"}\n\nevent: tok']);
    expect(events.map((e) => e.event)).toEqual(["status", "token"]);
  });

  it("handles \\r\\n line endings split between chunks, comments and multi-line data", async () => {
    const events = await collect([": keep-alive\r\nevent: message\r", "\ndata: line one\r\ndata: line two\r\n\r\n"]);
    expect(events).toEqual([{ event: "message", data: "line one\nline two" }]);
  });

  it("delivers a final event left unterminated", async () => {
    expect(await collect(['event: message\ndata: {"a": 1}'])).toEqual([{ event: "message", data: '{"a": 1}' }]);
  });

  it("parses the real recorded stream byte by byte", async () => {
    const events = await collect([...realStream]);
    expect(events.filter((e) => e.event === "status").length).toBeGreaterThan(3);
    expect(events.filter((e) => e.event === "token").length).toBeGreaterThan(0);
    expect(events.at(-1)?.event).toBe("message");
  });
});

describe("streamChat", () => {
  function respond(chunks: string[]) {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(chunked(chunks), { headers: { "Content-Type": "text/event-stream" } })));
  }

  it("passes on known events and ignores unknown ones", async () => {
    respond([
      'event: status\ndata: {"text": "assessing"}\n\n',
      'event: ping\ndata: {}\n\n',
      'event: token\ndata: {"text": "Hi"}\n\n',
      'event: message\ndata: {"conversation_id": "c", "kind": "answer", "text": "Hi.", "cards": [], "air_quality": [], "assessments": [], "sources": []}\n\n',
    ]);
    const seen: StreamEvent[] = [];
    await streamChat({ message: "hi" }, (e) => seen.push(e));
    expect(seen.map((e) => e.type)).toEqual(["status", "token", "message"]);
  });

  it("reports the error event", async () => {
    respond(['event: error\ndata: {"code": "agent_error", "message": "The weather service is busy."}\n\n']);
    const seen: StreamEvent[] = [];
    await streamChat({ message: "hi" }, (e) => seen.push(e));
    expect(seen).toEqual([{ type: "error", code: "agent_error", message: "The weather service is busy." }]);
  });
});
