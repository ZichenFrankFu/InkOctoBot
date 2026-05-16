// ui/frontend/src/api/client.ts

export const API_BASE = "";

const DEFAULT_TIMEOUT_MS = 30_000;
const UPLOAD_TIMEOUT_MS = 120_000;
const RETRY_DELAYS_MS = [1_000, 2_000];

function isRetryable(err: unknown, status?: number): boolean {
  // Network errors (offline, DNS failure, etc.)
  if (err instanceof TypeError || (err instanceof DOMException && err.name === "AbortError")) {
    return true;
  }
  // Server errors (502, 503, 504 are typically transient)
  if (status && status >= 502 && status <= 504) {
    return true;
  }
  return false;
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** Map HTTP errors to user-friendly Chinese messages */
function friendlyError(status: number, detail: string): string {
  if (detail) return detail;
  switch (status) {
    case 400: return "请求参数有误，请检查输入";
    case 401: return "未授权，请检查 API 密钥配置";
    case 403: return "没有权限执行此操作";
    case 404: return "请求的资源不存在";
    case 409: return "操作冲突，资源可能已被修改";
    case 413: return "上传的内容过大";
    case 422: return "数据格式不正确";
    case 429: return "请求过于频繁，请稍后重试";
    case 500: return "服务器内部错误，请稍后重试";
    case 502: return "服务暂时不可用，正在重试...";
    case 503: return "服务正在维护中，请稍后重试";
    case 504: return "请求超时，请稍后重试";
    default: return `请求失败 (HTTP ${status})`;
  }
}

interface RequestOptions extends RequestInit {
  timeoutMs?: number;
}

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const url = `${API_BASE}${path}`;
  const timeoutMs = init?.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);

    let status = 0;
    try {
      const { timeoutMs: _, ...fetchInit } = init || {};
      const res = await fetch(url, {
        ...fetchInit,
        headers: { "Content-Type": "application/json", ...(fetchInit?.headers || {}) },
        signal: controller.signal,
      });

      clearTimeout(timeout);
      status = res.status;

      if (!res.ok) {
        const text = await res.text().catch(() => "");
        let detail = "";
        try {
          const parsed = JSON.parse(text);
          detail = parsed.detail || "";
        } catch {
          detail = text;
        }

        // Check if this is a retryable server error
        if (attempt < RETRY_DELAYS_MS.length && isRetryable(null, status)) {
          await delay(RETRY_DELAYS_MS[attempt]);
          continue;
        }

        throw new Error(friendlyError(status, detail));
      }

      return res.json() as Promise<T>;
    } catch (err) {
      clearTimeout(timeout);

      const canRetry = attempt < RETRY_DELAYS_MS.length && isRetryable(err, status);
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

export function apiPost<T>(path: string, body: any, opts?: { timeoutMs?: number }): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}), ...opts });
}

export function apiPut<T>(path: string, body: any, opts?: { timeoutMs?: number }): Promise<T> {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}), ...opts });
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}

export function apiPatch<T>(path: string, body: any, opts?: { timeoutMs?: number }): Promise<T> {
  return request<T>(path, { method: "PATCH", body: JSON.stringify(body ?? {}), ...opts });
}
