// ui/frontend/src/utils/promptTemplate.ts
//
// Fetch a preset AI prompt template from the backend prompt registry
// (Settings → LLM Prompt tab) and interpolate its `{placeholder}` tokens.
//
// Pages that build a `system_hint` in TS use this so a user's saved
// prompt override actually takes effect. If the fetch fails for any
// reason the supplied `fallback` is used, so the UI never breaks.

import { apiGet } from "../api/client";

interface PromptResponse {
  key: string;
  current: string;
  vars: string[];
}

// Cache effective templates for the page session — these rarely change
// and every chat turn would otherwise re-fetch.
const _cache = new Map<string, string>();

/**
 * Interpolate `{name}` tokens in `template` with `vars[name]`.
 * Tokens with no matching var are left untouched. `{{` / `}}` are
 * un-escaped to literal braces (mirrors Python str.format()).
 */
export function interpolate(template: string, vars: Record<string, string>): string {
  return template
    .replace(/\{\{|\}\}|\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g, (m, name) => {
      if (m === "{{") return "{";
      if (m === "}}") return "}";
      return name in vars ? vars[name] : m;
    });
}

/**
 * Fetch the effective (possibly user-overridden) template for `key`
 * from the prompt registry and interpolate `vars` into it. Returns
 * `fallback` (interpolated) when the registry is unreachable.
 */
export async function renderPrompt(
  key: string,
  vars: Record<string, string>,
  fallback: string,
): Promise<string> {
  let template = _cache.get(key);
  if (template === undefined) {
    try {
      const r = await apiGet<PromptResponse>(`/api/references/prompts/${key}`);
      template = r.current || fallback;
      _cache.set(key, template);
    } catch {
      template = fallback;
    }
  }
  return interpolate(template, vars);
}
