/**
 * Split a free-text genre/category string into individual tags.
 *
 * Reference works often store multi-tag genres as a single comma-separated
 * string (e.g. "都市，异术超能，穿越"). Authors use a mix of separators:
 * the Chinese comma "，", ASCII comma ",", middle dot "·", "、",
 * semicolons, slashes, or whitespace. This helper normalises them all.
 */
export function splitGenres(s: string | undefined | null): string[] {
  if (!s) return [];
  const parts = s
    .split(/[，,·、/；;\s]+/)
    .map(t => t.trim())
    .filter(Boolean);
  // dedup while preserving order
  const seen = new Set<string>();
  const out: string[] = [];
  for (const p of parts) {
    if (!seen.has(p)) {
      seen.add(p);
      out.push(p);
    }
  }
  return out;
}
