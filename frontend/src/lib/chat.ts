/** The assistant's API: streamed chat turns and the client's conversations. */

import { request, send, type ChatRequest, type ChatResponse, type ConversationDetail, type ConversationOut } from "./api";
import { parseSse } from "./sse";

export type StreamEvent =
  | { type: "status"; text: string }
  | { type: "token"; text: string }
  | { type: "message"; response: ChatResponse }
  | { type: "error"; code: string; message: string };

/** Posts one turn to /chat/stream and reports its events. Errors before the stream starts (rate
 * limits, bad input, network) throw ApiError; an abort throws the AbortError. */
export async function streamChat(
  body: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await send("/api/v1/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.body) throw new Error("The response has no body to stream.");
  for await (const { event, data } of parseSse(response.body)) {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(data);
    } catch {
      continue; // not ours
    }
    if (event === "status" || event === "token") onEvent({ type: event, text: String(payload.text ?? "") });
    else if (event === "message") onEvent({ type: "message", response: payload as unknown as ChatResponse });
    else if (event === "error")
      onEvent({
        type: "error",
        code: String(payload.code ?? "error"),
        message: String(payload.message ?? "Something went wrong. Please try again."),
      });
    // anything else is ignored
  }
}

export function createConversation(): Promise<ConversationOut> {
  return request<ConversationOut>("/api/v1/conversations", { method: "POST" });
}

export function listConversations(): Promise<ConversationOut[]> {
  return request<ConversationOut[]>("/api/v1/conversations");
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return request<ConversationDetail>(`/api/v1/conversations/${encodeURIComponent(id)}`);
}

export async function deleteConversation(id: string): Promise<void> {
  await send(`/api/v1/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
}

const KEY = "ww.conversationId";

export function loadConversationId(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function saveConversationId(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(KEY, id);
    else window.localStorage.removeItem(KEY);
  } catch {
    // storage blocked: the chat just won't survive a refresh
  }
}
