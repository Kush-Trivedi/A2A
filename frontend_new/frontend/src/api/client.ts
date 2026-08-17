import { env } from "../config/env";

export const API_ENDPOINTS = {
  authMode: "/api/v1/auth/mode",
  authMe: "/api/v1/auth/me",
  authLogout: "/api/v1/auth/logout",
  sessions: "/api/v1/chat/sessions",
  sessionById: (sessionId: string) =>
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`,
  sessionMessages: (sessionId: string) =>
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
  editMessage: (sessionId: string, messageId: string) =>
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/edit`,
  messageFeedback: (sessionId: string, messageId: string) =>
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/feedback`,
  chat: "/api/v1/chat",
  chatStream: "/api/v1/chat/stream",
  filesUpload: "/api/v1/files/upload",
  sessionFile: (sessionId: string, sha256: string) =>
    `/api/v1/files/sessions/${encodeURIComponent(sessionId)}/${encodeURIComponent(sha256)}`,
  adminMyTeam: "/api/v1/admin/my-team",
  adminTeams: "/api/v1/admin/teams",
  adminTeamTokens: (teamKey: string) =>
    `/api/v1/admin/teams/${encodeURIComponent(teamKey)}/tokens`,
  adminAgents: "/api/v1/admin/agents",
  adminAgentStatus: (agentKey: string) =>
    `/api/v1/admin/agents/${encodeURIComponent(agentKey)}/status`,
  adminConnections: "/api/v1/admin/connections",
} as const;

interface RequestOptions extends RequestInit {
  authToken?: string;
  suppressLoginRedirect?: boolean;
}

const CSRF_COOKIE_NAME = "ace_session_csrf";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  const method = (options.method ?? "GET").toUpperCase();
  const isFormBody =
    typeof FormData !== "undefined" && options.body instanceof FormData;

  if (!headers.has("Content-Type") && options.body && !isFormBody) {
    headers.set("Content-Type", "application/json");
  }

  if (requiresCsrf(method)) {
    headers.set("X-CSRF-Token", getCsrfToken());
  }

  const response = await fetch(`${env.backendBaseUrl}${path}`, {
    ...options,
    credentials: "include",
    headers,
  });

  if (!response.ok) {
    const fallback = `Request failed with status ${response.status}`;
    let message = fallback;

    try {
      const body = (await response.json()) as {
        message?: string;
        detail?: string;
      };
      message = body.message ?? body.detail ?? fallback;
    } catch {
      message = fallback;
    }

    if (response.status === 401 && !options.suppressLoginRedirect) {
      redirectToBackendLogin();
    }

    throw new ApiError(message, response.status);
  }

  return (await response.json()) as T;
}

export function redirectToBackendLogin(): void {
  // Thin frontend: hand off the login redirect to the backend, which owns
  // the OAuth handshake. Preserve the current path so the user lands back
  // where they were after Entra returns.
  if (typeof window === "undefined") return;
  const returnTo = window.location.pathname + window.location.search;
  const params = new URLSearchParams({ return_to: returnTo });
  const url = `${env.backendBaseUrl}/api/v1/auth/login?${params.toString()}`;
  window.location.assign(url);
}

export async function logout(): Promise<void> {
  const response = await fetch(
    `${env.backendBaseUrl}${API_ENDPOINTS.authLogout}`,
    {
      method: "POST",
      credentials: "include",
      headers: { "X-CSRF-Token": getCsrfToken() },
    },
  );
  if (!response.ok && response.status !== 401) {
    throw new ApiError(
      `Logout failed with status ${response.status}`,
      response.status,
    );
  }
}

/**
 * Read the CSRF token from the non-httpOnly cookie the backend sets at login
 * (double-submit cookie pattern). There is no dedicated fetch endpoint — the
 * raw token only ever exists at the moment a session is created, so it is
 * handed to the browser via this readable cookie rather than an API call.
 */
export function getCsrfToken(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${CSRF_COOKIE_NAME}=`));
  return match
    ? decodeURIComponent(match.slice(CSRF_COOKIE_NAME.length + 1))
    : "";
}

function requiresCsrf(method: string): boolean {
  return !["GET", "HEAD", "OPTIONS"].includes(method);
}
