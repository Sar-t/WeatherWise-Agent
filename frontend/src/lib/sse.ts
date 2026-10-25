/** A Server-Sent Events parser over a fetch body (EventSource can't POST). Chunks can end anywhere:
 * mid-line, mid-event, or carry several events; lines may end in \n, \r\n or \r. */

export type SseEvent = { event: string; data: string };

export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "";
  let data: string[] = [];

  function* lines(final: boolean): Generator<string> {
    const parts = buffer.split(/\r\n|\r|\n/);
    buffer = final ? "" : parts.pop()!; // the last part is an unfinished line, unless the stream ended
    yield* parts;
  }

  function* dispatch(line: string): Generator<SseEvent> {
    if (line === "") {
      if (data.length) yield { event: event || "message", data: data.join("\n") };
      event = "";
      data = [];
      return;
    }
    if (line.startsWith(":")) return; // a comment
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      if (buffer.endsWith("\r")) continue; // might be the first half of "\r\n"
      for (const line of lines(false)) yield* dispatch(line);
    }
    buffer += decoder.decode();
    for (const line of lines(true)) yield* dispatch(line);
    yield* dispatch(""); // an event left unterminated at the end of the stream
  } finally {
    reader.releaseLock();
  }
}
