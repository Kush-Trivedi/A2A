import type { StreamEvent } from "../types";
import { AceApiClient } from "./AceApiClient";

/** Owns one streaming chat turn: POST + SSE parsing + typed event callbacks. */
export class ChatStreamController {
  private aborter: AbortController | null = null;

  constructor(private readonly api: AceApiClient) {}

  abort(): void {
    this.aborter?.abort();
    this.aborter = null;
  }

  async stream(
    payload: { message: string; agent: string | null; session_id: string | null },
    onEvent: (event: StreamEvent) => void,
  ): Promise<void> {
    this.abort();
    this.aborter = new AbortController();

    const response = await fetch("/api/v1/chat/stream", {
      method: "POST",
      credentials: "same-origin",
      signal: this.aborter.signal,
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": this.api.csrfToken(),
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok || !response.body) {
      throw new Error(`stream failed: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = this.parse(raw);
        if (event) onEvent(event);
        boundary = buffer.indexOf("\n\n");
      }
    }
  }

  private parse(raw: string): StreamEvent | null {
    let name = "message";
    let data = "";
    for (const line of raw.split("\n")) {
      if (line.startsWith("event: ")) name = line.slice(7).trim();
      else if (line.startsWith("data: ")) data += line.slice(6);
    }
    if (!data) return null;
    try {
      return { event: name, data: JSON.parse(data) } as StreamEvent;
    } catch {
      return null;
    }
  }
}
