const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";
let csrfToken = "";
let workspaceId = "";

export function setCsrfToken(value: string) {
  csrfToken = value;
}

export function setWorkspaceId(value: string) {
  workspaceId = value;
}

export async function refreshCsrfToken(): Promise<string> {
  const response = await fetch(`${API_BASE}/api/auth/csrf`, { credentials: "include" });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Could not initialize the secure session.");
  csrfToken = body.csrf_token;
  return csrfToken;
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (workspaceId) headers.set("X-Workspace-ID", workspaceId);
  if (!(["GET", "HEAD", "OPTIONS"].includes(method)) && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  return fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" });
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.detail || `Request failed (${response.status})`) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return body as T;
}

export { API_BASE };
