// ui/frontend/src/api/swr.ts
//
// 秒开 helpers (spec UI设计·机制3/机制4 响应速度·缓存与懒加载):
//
// - swrHydrate / swrStore: sessionStorage-backed stale-while-revalidate.
//   Pages hydrate state SYNCHRONOUSLY from the last visit (zero 加载中
//   flash on revisit), then refetch in the background and re-store.
//
// - ComputeEnvelope + pollCompute: client side of the backend
//   compute_cache protocol. Heavy analyses ({state:'computing'}) are
//   polled until ready; ready payloads carry a `stale` flag so the UI
//   can show a 数据已更新 banner instead of silently recomputing.

import { apiGet } from "./client";

const PREFIX = "inkswr_v1_";

export function swrHydrate<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(PREFIX + key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function swrStore(key: string, value: unknown): void {
  try {
    sessionStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* quota — stale hydrate is only an optimization */
  }
}

/* ── backend compute_cache protocol ── */

export interface ComputeEnvelope<T> {
  state: "ready" | "computing" | "empty" | "error";
  payload?: T;
  stale?: boolean;
  computing?: boolean;
  updated_at?: number | null;
  error?: string;
}

export interface PollHandlers<T> {
  /** Fired on every ready envelope (initial + after background refresh). */
  onReady: (payload: T, env: ComputeEnvelope<T>) => void;
  /** Nothing cached yet and the request was cached_only. */
  onEmpty?: () => void;
  onError?: (message: string) => void;
  /** Still computing (poll continues automatically). */
  onComputing?: () => void;
}

export interface PollController {
  cancel: () => void;
}

/**
 * Drive a compute_cache endpoint to completion.
 *
 * `url` must already include refresh/cached_only params for the FIRST
 * request; follow-up polls strip them (plain reads). Polling stops when
 * the envelope is ready-and-not-computing, empty, or error — or when
 * the caller cancels (page unmount).
 */
export function pollCompute<T>(
  url: string,
  handlers: PollHandlers<T>,
  intervalMs = 2500,
): PollController {
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const plainUrl = (() => {
    try {
      const u = new URL(url, window.location.origin);
      u.searchParams.delete("refresh");
      u.searchParams.delete("cached_only");
      return u.pathname + (u.search || "");
    } catch {
      return url;
    }
  })();

  const tick = (target: string) => {
    apiGet<ComputeEnvelope<T>>(target)
      .then((env) => {
        if (cancelled) return;
        if (env.state === "ready") {
          handlers.onReady(env.payload as T, env);
          if (env.computing) {
            timer = setTimeout(() => tick(plainUrl), intervalMs);
          }
        } else if (env.state === "computing") {
          handlers.onComputing?.();
          timer = setTimeout(() => tick(plainUrl), intervalMs);
        } else if (env.state === "empty") {
          handlers.onEmpty?.();
        } else {
          handlers.onError?.(env.error || "分析失败");
        }
      })
      .catch((e) => {
        if (cancelled) return;
        handlers.onError?.(e?.message || String(e));
      });
  };

  tick(url);
  return {
    cancel: () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    },
  };
}
