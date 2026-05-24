/* Shared segmentation cache for a reference work.
 *
 * The Chronicle, Characters, and Settings tabs all need the same
 * segment plan + per-segment chunk lists. Before this module existed
 * each tab fetched its own copy, which (a) cost extra HTTP roundtrips
 * on tab switches and (b) let the tabs disagree when one of them
 * cached stale data. Now they all subscribe to one Map keyed by refId
 * and the first tab that asks for a piece of data fetches it for the
 * rest.
 *
 * Why a module-level Map instead of React Context:
 *   - The data outlives any single component tree. When the user
 *     selects a different work in the left panel React unmounts the
 *     old tabs and mounts new ones — a Context provider would tear
 *     down with them, losing the cache. The module cache survives.
 *   - Multiple work selections accumulate in the cache; we don't
 *     refetch when the user toggles back to a previously-viewed work.
 * Memory is bounded by user behaviour (one entry per work touched
 * this session); we don't bother evicting since the payloads are
 * small JSON metadata, not chapter text.
 */
import { useEffect, useState } from "react";
import { apiGet } from "../../api/client";

export interface SegmentInfo {
  index: number;
  title: string;
  /** Volume ordinal (第N卷 / 上卷 / 卷一) — stored separately from the
   *  volume name so the UI can render them as two tags. */
  volume_no?: string;
  start_chapter: number;
  end_chapter: number;
  chapter_count?: number;
  char_count: number;
}

export interface SegmentPlan {
  type: "volumes" | "chunks" | "custom";
  segments: SegmentInfo[];
  completed: number[];
  total_chapters: number;
  is_custom?: boolean;
}

export interface ChunkMeta {
  chunk_index: number;
  total_chunks: number;
  start_chapter: number;
  end_chapter: number;
  n_chapters: number;
  n_chars: number;
}

interface Entry {
  plan: SegmentPlan | null;
  planLoading: boolean;
  chunks: Record<number, ChunkMeta[]>;
  chunkLoading: Set<number>;
  subscribers: Set<() => void>;
}

const cache = new Map<string, Entry>();

function getEntry(refId: string): Entry {
  let e = cache.get(refId);
  if (!e) {
    e = {
      plan: null, planLoading: false,
      chunks: {}, chunkLoading: new Set(),
      subscribers: new Set(),
    };
    cache.set(refId, e);
  }
  return e;
}

function notify(refId: string) {
  const e = cache.get(refId);
  if (!e) return;
  for (const fn of e.subscribers) fn();
}

async function loadPlanInto(refId: string): Promise<void> {
  const e = getEntry(refId);
  if (e.plan || e.planLoading) return;
  e.planLoading = true;
  notify(refId);
  try {
    const p = await apiGet<SegmentPlan>(`/api/references/works/${refId}/segments/plan`);
    e.plan = p;
  } catch {
    /* keep null; subscribers can retry by remounting */
  } finally {
    e.planLoading = false;
    notify(refId);
  }
}

async function loadChunksInto(refId: string, segIdx: number): Promise<void> {
  const e = getEntry(refId);
  if (e.chunks[segIdx] || e.chunkLoading.has(segIdx)) return;
  e.chunkLoading.add(segIdx);
  notify(refId);
  try {
    const r = await apiGet<{ chunks: ChunkMeta[] }>(
      `/api/references/works/${refId}/segments/${segIdx}/chunks`,
    );
    // Replace (not mutate) the chunks map so its reference changes —
    // consumers memoize on it (e.g. chunkList) and would otherwise miss
    // the update and keep showing an empty chunk list.
    e.chunks = { ...e.chunks, [segIdx]: r.chunks || [] };
  } catch { /* silent */ }
  finally {
    e.chunkLoading.delete(segIdx);
    notify(refId);
  }
}

/** Invalidate cached plan + chunks for a work — call after the user
 *  saves a new segment plan in the preprocess tab so the next read
 *  sees the new layout. */
export function invalidateSegmentation(refId: string) {
  const e = cache.get(refId);
  if (!e) return;
  e.plan = null;
  e.chunks = {};
  notify(refId);
}

/** Subscribe a component to a work's shared segmentation. Returns
 *  the current snapshot + an `ensureChunks(segIdx)` action. */
export function useSegmentation(refId: string, hasFullText: boolean) {
  // Tick state forces re-render when the cache notifies us; the
  // actual data is read from the module Map below.
  const [, setTick] = useState(0);
  const e = getEntry(refId);

  useEffect(() => {
    if (!refId) return;
    const sub = () => setTick(t => t + 1);
    e.subscribers.add(sub);
    if (hasFullText && !e.plan && !e.planLoading) {
      void loadPlanInto(refId);
    }
    return () => {
      e.subscribers.delete(sub);
    };
  }, [refId, hasFullText, e]);

  return {
    plan: e.plan,
    planLoading: e.planLoading,
    chunks: e.chunks,
    chunkLoading: e.chunkLoading,
    ensureChunks: (segIdx: number) => loadChunksInto(refId, segIdx),
    reloadPlan: () => {
      e.plan = null;
      void loadPlanInto(refId);
    },
  };
}
