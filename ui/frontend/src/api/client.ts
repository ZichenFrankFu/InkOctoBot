// ui/frontend/src/api/client.ts

export const API_BASE = "";

const DEFAULT_TIMEOUT_MS = 30_000;
const RETRY_DELAYS_MS = [1_000, 2_000];

function isNetworkError(err: unknown): boolean {
  return err instanceof TypeError || (err instanceof DOMException && err.name === "AbortError");
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;

  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

    try {
      const res = await fetch(url, {
        ...init,
        headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
        signal: controller.signal,
      });

      clearTimeout(timeout);

      if (!res.ok) {
        const text = await res.text().catch(() => "");
        // Try to extract detail from FastAPI JSON error response
        let detail = "";
        try {
          const parsed = JSON.parse(text);
          detail = parsed.detail || "";
        } catch {
          detail = text;
        }
        throw new Error(detail || `HTTP ${res.status} ${res.statusText} @ ${url}`);
      }

      return res.json() as Promise<T>;
    } catch (err) {
      clearTimeout(timeout);

      const canRetry = attempt < RETRY_DELAYS_MS.length && isNetworkError(err);
      if (!canRetry) throw err;

      await delay(RETRY_DELAYS_MS[attempt]);
    }
  }

  // Unreachable, but satisfies TypeScript
  throw new Error("request failed");
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path, { method: "GET" });
}

export function apiPost<T>(path: string, body: any): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
}

export function apiPut<T>(path: string, body: any): Promise<T> {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) });
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}