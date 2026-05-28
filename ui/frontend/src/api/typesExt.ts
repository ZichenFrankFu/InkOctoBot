// ── Manual paste inbox types ────────────────────────────
export interface PendingPaste {
  paste_token: string;
  call_site_id: string;
  project_id: string;
  chapter_id: string;
  prompt_chars: number;
  created_at: number;
  age_seconds: number;
}

export interface PendingPasteDetail {
  paste_token: string;
  call_site_id: string;
  project_id: string;
  chapter_id: string;
  prompt: string;
  system: string;
  created_at: number;
}

// ── Part B: Domain learning ─────────────────────────────
export type DomainStatus =
  | "proposed" | "rejected" | "compiling"
  | "needs_review" | "accepted" | "failed";

export interface DomainSuggestion {
  suggestion_id: string;
  project_id: string;
  domain: string;
  sub_domain: string;
  rationale: string;
  status: DomainStatus;
  compiled_content: string;
  compiled_content_preview: string;
  has_compiled_content: boolean;
  skill_name: string;
  source_mode: string;
  created_at: number;
  updated_at: number;
}

export interface DomainReview {
  suggestion_id: string;
  status: DomainStatus;
  domain: string;
  sub_domain: string;
  rationale: string;
  source_mode: string;
  compiled_content: string;
  skill_name: string;
}

// ── Part A: Edit learning ───────────────────────────────
export type PreferenceType = "style" | "content" | "pacing";

export interface UserStylePreference {
  pref_id: string;
  project_id: string;
  preference_type: PreferenceType;
  description: string;
  confidence: number;
  observation_count: number;
  is_confirmed: number;
  created_at: string;
  updated_at: string;
}

export interface EditObservation {
  obs_id: string;
  project_id: string;
  chapter_id: string;
  chapter_num: number;
  special_requirement: string;
  diff_stats_json: string;
  consumed: number;
  created_at: number;
}

// ── Truth state review ──────────────────────────────────
export interface ChapterAuditRow {
  chapter_id: string;
  chapter_num: number;
  title: string;
  audit_status: "audit_pending" | "audit_passed" | "audit_failed" | "audit_overridden";
  audit_issues: { type: string; severity: string; description: string }[];
  updated_at: string;
}
