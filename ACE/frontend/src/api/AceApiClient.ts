import type { AgentSummary, ChatMessage, SessionSummary, UserProfile } from "../types";

interface Envelope<T> {
  data: T;
  message?: string;
}

/** Typed API layer — the only place the frontend talks HTTP. */
export class AceApiClient {
  private csrfCookieName = "ace_session_csrf";

  csrfToken(): string {
    const match = document.cookie
      .split("; ")
      .find((row) => row.startsWith(`${this.csrfCookieName}=`));
    return match ? decodeURIComponent(match.split("=")[1]) : "";
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": this.csrfToken(),
        ...(init?.headers ?? {}),
      },
      ...init,
    });
    if (response.status === 401) {
      throw new UnauthenticatedError();
    }
    if (!response.ok) {
      throw new Error(`${path} failed: ${response.status}`);
    }
    const body = (await response.json()) as Envelope<T>;
    return body.data;
  }

  me(): Promise<{ user: UserProfile }> {
    return this.request("/api/v1/auth/me");
  }

  loginUrl(): string {
    return "/api/v1/auth/login";
  }

  agents(): Promise<AgentSummary[]> {
    return this.request("/api/v1/chat/agents");
  }

  sessions(): Promise<SessionSummary[]> {
    return this.request("/api/v1/chat/sessions");
  }

  messages(sessionId: string): Promise<{ session_id: string; messages: ChatMessage[] }> {
    return this.request(`/api/v1/chat/sessions/${sessionId}/messages`);
  }

  logout(): Promise<Record<string, unknown>> {
    return this.request("/api/v1/auth/logout", { method: "POST" });
  }

  async uploadFile(sessionId: string, file: File): Promise<{ chunk_count: number }> {
    const form = new FormData();
    form.append("knowledge_source", "upload");
    form.append("session_id", sessionId);
    form.append("file", file);
    const response = await fetch("/api/v1/knowledge/ingest/file", {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": this.csrfToken() },
      body: form,
    });
    if (response.status === 401) throw new UnauthenticatedError();
    if (!response.ok) throw new Error(`upload failed: ${response.status}`);
    return (await response.json()).data;
  }
}

export class UnauthenticatedError extends Error {
  constructor() {
    super("unauthenticated");
  }
}

export const apiClient = new AceApiClient();
