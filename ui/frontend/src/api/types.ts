// ═══════════════════════════════════════
// InkOctoBot TypeScript Type Definitions
// ═══════════════════════════════════════

// ── Project ──
export interface Project {
  id: string;
  name: string;
  genre?: string;
  platform?: string;          // 平台: 起点/番茄/etc
  gender_target?: string;     // 男频/女频
  serial_status?: string;     // 连载状态
  synopsis?: string;          // 故事梗概
  status?: string;
  created_at?: string | number;
  updated_at?: string | number;
  word_count?: number;
  chapter_count?: number;
}

// ── Market Data ──
export interface Novel {
  novel_uid: number;
  platform: string;
  platform_novel_id?: string;
  author: string;
  author_norm?: string;
  intro?: string;
  intro_norm?: string;
  main_category?: string;
  status?: string;
  total_words?: number;
  url?: string;
  created_date?: string;
  last_seen_date?: string;
  titles?: NovelTitle[];
  tags?: string[];
}

export interface NovelTitle {
  title_id: number;
  novel_uid: number;
  title: string;
  title_norm?: string;
  is_primary: boolean;
}

export interface RankList {
  rank_list_id: number;
  platform: string;
  rank_family: string;
  rank_sub_cat?: string;
  source_url?: string;
}

export interface RankSnapshot {
  snapshot_id: number;
  rank_list_id: number;
  snapshot_date: string;
  item_count: number;
}

export interface RankEntry {
  snapshot_id: number;
  novel_uid: number;
  rank: number;
  total_recommend?: number;
  reading_count?: number;
  extra_json?: string;
  novel?: Novel;
}

export interface Chapter {
  chapter_id: number;
  novel_uid: number;
  chapter_num: number;
  chapter_title?: string;
  chapter_content?: string;
  word_count?: number;
}

// ── Reference Works ──
export type MediaType = "web_novel" | "literature" | "poetry" | "film" | "anime" | "tv_series" | "other";
export type ContentSource = "platform_crawl" | "file_upload" | "manual";
export type PreprocessingStatus = "not_applicable" | "pending" | "processing" | "done";

export interface ReferenceWork {
  ref_id: string;
  title: string;
  creator?: string;
  media_type: MediaType;
  genre?: string;
  tags_json?: string;
  source: ContentSource;
  platform?: string;
  novel_uid?: number;
  file_path?: string;
  user_rating?: number;
  user_summary?: string;
  user_why_i_like?: string;
  learning_dimensions_json?: string;
  has_full_text?: boolean;
  preprocessing_status: PreprocessingStatus;
  style_fingerprint_json?: string;
  narrative_structure_json?: string;
  extracted_characters_json?: string;
  rhythm_template_json?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ReferenceEntry {
  entry_id: string;
  ref_id: string;
  entry_type: string;
  title: string;
  content: string;
  content_source?: string;
  position_label?: string;
  user_notes?: string;
  learning_dimensions_json?: string;
  user_rating?: number;
  tags_json?: string;
  created_at?: string;
}

// ── Characters ──
export interface Character {
  id: string;
  project_id: string;
  name: string;
  gender?: string;           // 性别: 男/女/其他
  age?: string;              // 年龄 (free text, e.g. "25", "不详")
  role?: string;             // 角色定位: 主角/配角/反派/路人
  tags?: string[];
  personality?: string;      // 性格核心记忆点
  background?: string;       // 背景故事
  speech_style?: string;     // 口癖
  appearance?: string;       // 外貌核心记忆点
  layer_b?: CharacterLayerB;
  relationships?: CharacterRelationship[];
  dynamic_snapshots?: DynamicPropertySnapshot[];
}

export interface CharacterLayerB {
  loss_aversion: number;
  risk_aversion_gain: number;
  risk_aversion_loss: number;
  impulse_probability: number;
  social_frequency: number;
  time_discount: number;
  value_weights: Record<string, number>;
}

export interface CharacterRelationship {
  target_id: string;
  target_name: string;
  affinity: number;      // 好感度: -100 to 100, higher = more approval
  priority: number;      // 优先级: 1+, lower = higher priority (includes self)
  chapter?: string;       // 时间戳: chapter or in-story time marker
  notes?: string;
  label?: string;        // 关系标签 e.g. "师徒", "情侣", "宿敌"
}

// ── Dynamic Property Snapshot (chapter-linked) ──
export interface DynamicPropertySnapshot {
  chapter: string;        // chapter or in-story time marker
  personality?: string;
  background?: string;
  speech_style?: string;
  notes?: string;
  relationships?: CharacterRelationship[];
  layer_b?: CharacterLayerB;
}

// ── World Book ──
export type WorldBookCategory = "power_system" | "factions" | "geography" | "social_rules" | "history" | "hard_rules" | "other" | string;

export interface WorldBookEntry {
  id: string;
  project_id: string;
  title: string;
  category: WorldBookCategory;
  content: string;
  tags?: string[];
  created_at?: string;
  updated_at?: string;
}

// ── Editor / Chapters ──
export interface Volume {
  id: string;
  project_id: string;
  title: string;
  order: number;
  chapters: ChapterOutline[];
}

export interface ChapterOutline {
  id: string;
  volume_id: string;
  title: string;
  order: number;
  synopsis?: string;
  content?: string;
  word_count?: number;
  status?: "draft" | "generating" | "review" | "final";
  time?: string;
  location?: string;
  characters?: string[];
  references?: string[];
  /** Map of real character name -> alias for hidden identity (e.g. "李悦" -> "神秘女人") */
  character_aliases?: Record<string, string>;
}

// ── Pipeline / Generation ──
export interface PipelineStatus {
  step: string;
  status: "pending" | "running" | "done" | "error";
  detail?: string;
  progress?: number;
  elapsed?: number;
}

// ── Follow-up Questions (brainstorm / pipeline interactive) ──
export interface FollowUpQuestion {
  text: string;
  options: string[];
}

// ── Calibration Feedback ──
export interface SampleFeedback {
  score: number;       // 1-5
  comment: string;
  confirmed: boolean;
}

export interface CalibrationHistory {
  sample: string;
  feedback: SampleFeedback;
  analysis?: string;
}

// ── Agent Warning (pipeline interactive) ──
export interface AgentWarning {
  step: string;
  agent: string;
  message: string;
  severity?: "info" | "warning" | "error";
  options?: string[];
}

// ── Extended Chat Message for pipeline ──
export interface PipelineChatMessage {
  agent: string;
  content: string;
  status?: "thinking" | "speaking" | "done" | "waiting_confirm";
  timestamp: number;
  isQuestion?: boolean;
  isWarning?: boolean;
  isCoT?: boolean;
  promptSent?: string;
  followUpOptions?: string[];
  warningOptions?: string[];
  agentDisplayName?: string;
}

export interface GenerationResult {
  chapter_id: string;
  text: string;
  model_used: string;
  tokens_used: number;
  cost_usd?: number;
  version: number;
}

// ── Version History ──
export interface TextVersion {
  version_id: string;
  chapter_id: string;
  version: number;
  source: "ai_generated" | "user_edited" | "targeted_rewrite" | "outline_chat" | "auto_saved";
  text: string;
  synopsis?: string;
  model_used?: string;
  created_at: string;
}

// ── Evaluation ──
export interface EvalResult {
  chapter_id: string;
  passed: boolean;
  score: number;
  issues: EvalIssue[];
  process?: EvalProcessStep[];
  strengths?: string[];
  summary?: string;
  summary_text?: string;
  dimension_scores?: Record<string, number>;
  process_log?: { detector: string; status: string; detail: string }[];
}

export interface EvalProcessStep {
  detector: string;
  status: "running" | "done" | "skipped" | "error";
  detail: string;
  findings?: string[];
  llm_score?: number;
}

export interface EvalIssue {
  type: string;
  severity: "high" | "medium" | "low";
  description: string;
  paragraph_index?: number;
  suggestion?: string;
}

// ── Storyline / Timeline ──
export interface StoryNode {
  id: string;
  title: string;
  chapter_num?: number;
  summary?: string;
  characters?: string[];
  color?: string;
  x: number;
  y: number;
  time?: string;
  location?: string;
  week?: number;
}

export interface StoryEdge {
  id: string;
  from: string;
  to: string;
  label?: string;
}

// ── Memory System ──
export interface EpisodicEvent {
  event_id: string;
  chapter_num: number;
  event_type: "plot" | "character" | "foreshadow" | "revelation";
  description: string;
  characters_involved: string[];
  foreshadow_status?: "planted" | "hinted" | "resolved";
  linked_event_id?: string;
  timestamp?: string;
}

// ── Model / Provider ──
export interface ModelProvider {
  id: string;
  name: string;
  type: "openai" | "anthropic" | "deepseek" | "gemini" | "ollama" | "vllm" | "local";
  api_key_set?: boolean;
  base_url?: string;
  models?: string[];
}

export interface ModelAssignment {
  role: string;
  provider: string;
  model: string;
}

// ── Agent Events ──
export interface AgentEvent {
  event_type: string;
  agent: string;
  message: string;
  severity?: "info" | "warning" | "suggestion";
  timestamp: string;
  data?: Record<string, unknown>;
}

// ── Settings ──
export interface ProviderConfig {
  enabled: boolean;
  api_key?: string;
  base_url?: string;
  models: string[];
}

export interface PipelineRole {
  provider: string;
  model: string;
  compare_models: string[];
}

export interface AppSettings {
  theme: string;
  auto_save: boolean;
  auto_save_interval: number;
  max_backup_versions: number;
  cost_confirm: boolean;
  export_format: "txt" | "docx" | "epub";
  providers: Record<string, ProviderConfig>;
  pipeline: Record<string, PipelineRole>;
  crawler_db_path?: string;
}

// ── Analysis ──
export interface TrendData {
  tag: string;
  heat_slope: number;
  share_slope: number;
  latest_count: number;
  latest_share: number;
}

export interface CategoryTrend {
  category: string;
  count_slope: number;
  latest_count: number;
}

// ── Config (legacy) ──
export interface ConfigSchema {
  defaults: Record<string, unknown>;
  platforms?: string[];
  rank_keys: { qidian: string[]; fanqie: string[] };
  notes: Record<string, string>;
}

export interface ConfigRun {
  run_id: string;
  created_at: number;
  config: Record<string, unknown>;
}

export interface Task {
  task_id: string;
  task_type: string;
  status: string;
  created_at: number;
  started_at?: number | null;
  ended_at?: number | null;
  config_run_id?: string | null;
  command?: string[] | null;
  log_path?: string | null;
  exit_code?: number | null;
}

// ── Skills ──
export interface SkillInfo {
  name: string;
  display_name: string;
  description: string;
  version: string;
  model_role: string;
  max_tokens: number;
  temperature: number;
  tags: string[];
  permissions: string[];
  learnable: boolean;
  is_learned: boolean;
  agent_domain: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  skill_md?: string;
}

export interface SkillExecuteResult {
  status: string;
  skill_name: string;
  result: Record<string, unknown>;
  execution_time_ms: number;
  model_used?: string;
}
