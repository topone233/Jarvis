// Hand-written mirrors of the shapes the Core API returns.
//
// `backend/app/database.py` (the tables) and `backend/app/schemas.py` (the
// request bodies) are the source of truth. There is no codegen step on purpose:
// the surface is small, and a generator plus its config would be more machinery
// than the drift it prevents.

export type RunStatus =
  'running' | 'cancelling' | 'completed' | 'cancelled' | 'failed' | 'interrupted'

export type MessageRole = 'user' | 'assistant' | 'system'

/** One retrieved knowledge chunk, as `KnowledgeService.search` returns it. */
export interface Citation {
  chunk_id: string
  document_id: string
  title: string
  /** The section the chunk lives in; null for a headingless document. */
  section_id?: string | null
  section_title?: string | null
  content: string
  score: number
  source: string
  /** The `[n]` the system instruction assigns; absent on older messages. */
  number?: number
}

/** One heading of a stored document, as the content endpoint returns it. */
export interface KnowledgeSectionRef {
  id: string
  level: number
  title: string
  start: number
  end: number
}

/** The canonical text of a knowledge document, for the citation panel. */
export interface KnowledgeContent {
  id: string
  title: string
  original_filename: string
  content: string
  sections: KnowledgeSectionRef[]
}

/**
 * Everything the assistant message carries besides its text. Only `run_id` is
 * guaranteed; the rest appear as the run gets far enough to know them.
 */
export interface MessageMetadata {
  run_id?: string
  citations?: Citation[]
  reasoning?: string
  usage?: Record<string, number> | null
  cancelled?: boolean
  error?: string
  context_artifact_id?: string | null
  /** User messages only: object filenames of the images pasted with it. */
  images?: string[]
}

export interface Message {
  id: string
  conversation_id: string
  parent_id: string | null
  role: MessageRole
  content: string
  ordinal: number
  metadata: MessageMetadata
  created_at: string
  updated_at: string
  deleted_at: string | null
}

export interface Run {
  id: string
  conversation_id: string
  user_message_id: string
  assistant_message_id: string
  model_profile_id: string
  status: RunStatus
  error_message: string | null
  input_token_estimate: number
  output_token_estimate: number
  started_at: string
  completed_at: string | null
}

export interface Conversation {
  id: string
  project_id: string | null
  title: string
  model_profile_id: string | null
  is_pinned: boolean
  created_at: string
  updated_at: string
  deleted_at: string | null
}

/**
 * One configured model, as `GET /api/model-profiles` returns it.
 *
 * `is_default` and `has_api_key` are booleans, not the 0/1 the columns hold:
 * `store.py` passes them through `_record(..., bool_fields=...)`. The API key
 * itself is never in here - it lives in the OS credential manager, and
 * `has_api_key` is all the server will say about it.
 */
export interface ModelProfile {
  id: string
  name: string
  base_url: string
  protocol: string
  chat_model: string
  context_window: number
  output_token_reserve: number
  /**
   * The generation limit. Null is not "a very large number": it means the field
   * is left out of the request entirely, so the endpoint applies its own.
   */
  max_tokens: number | null
  /** A percentage of the input budget: how full it may get before compaction. */
  compact_percent: number
  /**
   * What the endpoint is told when the thinking dial is at 关, and what it used
   * to be told when the dial was a switch. The keys are the endpoint's own
   * dialect and nothing here reads them: `{"enable_thinking": false}`,
   * `{"reasoning_effort": "low"}` and `{"thinking": {"type": "disabled"}}` are
   * three spellings of the same wish, and a fourth exists somewhere.
   *
   * `thinking_on` is read by nobody and sent to nobody since the dial arrived:
   * the three strengths send `reasoning_effort`, this app's own field name, not
   * a fragment. It is still stored and still round-trips, so editing a profile
   * does not throw away what was typed into it while the switch existed.
   *
   * An empty `thinking_off` means this endpoint is never told anything, which is
   * not the same as telling it "no".
   */
  thinking_on: Record<string, unknown>
  thinking_off: Record<string, unknown>
  is_default: boolean
  has_api_key: boolean
  created_at: string
  updated_at: string
  deleted_at: string | null
}

/**
 * The body of a profile create or update (`schemas.py`'s `ModelProfileCreate`).
 *
 * `api_key` is write-only: leaving it out of an update keeps the stored one,
 * while sending an empty string deletes it. `api/profiles.ts` is where that is
 * turned into a request.
 */
export interface ModelProfileInput {
  name: string
  base_url: string
  chat_model: string
  api_key?: string | null
  context_window?: number
  output_token_reserve?: number
  max_tokens?: number | null
  compact_percent?: number
  thinking_on?: Record<string, unknown>
  thinking_off?: Record<string, unknown>
  is_default?: boolean
}

/** One prompt on the settings screen (`backend/app/settings.py`). */
export interface PromptSetting {
  /** The text in force: stored one if there is one, the built-in one if not. */
  text: string
  /** What the code ships, and what "restore" puts back. */
  default_text: string
  /** False once the user has saved text of their own. */
  is_default: boolean
}

export interface QuickPrompt {
  /** What the button says. */
  name: string
  /** What one click fills the composer with. */
  prompt: string
}

export interface AppSettings {
  prompts: {
    system_prompt: PromptSetting
    compaction_prompt: PromptSetting
    memory_prompt: PromptSetting
  }
  quick_prompts: {
    /** In draw order; empty means the buttons were cleared on purpose. */
    items: QuickPrompt[]
    is_default: boolean
  }
  tool_limits: {
    max_rounds: ToolLimitSetting
    repeat_window_seconds: ToolLimitSetting
    repeat_limit: ToolLimitSetting
  }
  bash_tool: {
    enabled: { value: boolean; is_default: boolean }
    /** Empty means no row: commands start in the data directory. */
    working_dir: { value: string; is_default: boolean }
    grace_seconds: ToolLimitSetting
    /** "ask" holds each command for the user's approval; "grace" is the
     *  fixed buffer before it runs. */
    approval_mode: { value: 'ask' | 'grace'; default: 'ask' | 'grace'; is_default: boolean }
  }
}

/** One number a tool-call setting holds: what is in force, what the code ships. */
export interface ToolLimitSetting {
  value: number
  default: number
  is_default: boolean
}

/** The tool-calls tab's number inputs, by draft key. A literal union rather
 *  than a keyof on purpose: the bash grace window drafts alongside the three
 *  budget numbers but lives under `bash_tool` in the overview. */
export type ToolLimitKey =
  'max_rounds' | 'repeat_window_seconds' | 'repeat_limit' | 'bash_grace_seconds'

export type PromptKey = keyof AppSettings['prompts']

/** A save: the text to store, or null to go back to the built-in one. */
export type PromptPatch = Partial<Record<PromptKey, string | null>>

/** Anything the settings PUT accepts: prompt texts and the quick-prompt list. */
export type SettingsPatch = PromptPatch & {
  quick_prompts?: QuickPrompt[] | null
  tool_max_rounds?: number | null
  tool_repeat_window_seconds?: number | null
  tool_repeat_limit?: number | null
  /** The switch's deal: on sends null (deleting the row IS the default-on
   *  state), off sends false. */
  bash_enabled?: boolean | null
  bash_working_dir?: string | null
  bash_grace_seconds?: number | null
  bash_approval_mode?: 'ask' | 'grace' | null
}

/** One row of the audit trail (`run_events`). Also what `audit` SSE events carry. */
export interface RunEventRecord {
  id: string
  run_id: string
  sequence: number
  stage: string
  state: string
  payload: Record<string, unknown>
  created_at: string
}

/**
 * One remembered fact, as `GET /api/memories` returns it (`memories` table).
 * Only active rows come back - superseded and deleted ones are the backend's
 * history, not the page's.
 */
export interface Memory {
  id: string
  /** `global`, or `project` when it belongs to one project. */
  scope: string
  project_id: string | null
  kind: string
  memory_key: string
  content: string
  confidence: number
  confirmation_count: number
  /** The message the fact was said in. Null once that message was deleted. */
  source_message_id: string | null
  source_excerpt: string
  created_at: string
  updated_at: string
}

/** The body of PATCH /api/memories/{id}: keys left out keep their value. */
export interface MemoryUpdate {
  memory_key?: string
  content?: string
  confidence?: number
}

/**
 * One imported knowledge document (`GET /api/knowledge/documents`, active rows
 * of `knowledge_documents`). The file's bytes live in the data directory's
 * `objects/`; this row is the index's handle on it.
 */
export interface KnowledgeDocument {
  id: string
  project_id: string | null
  title: string
  original_filename: string
  relative_path: string | null
  mime_type: string
  content_hash: string
  stored_path: string
  /** `ready`, or `ready_without_embeddings` when the embedding call failed. */
  status: string
  chunk_count: number
  created_at: string
  updated_at: string
}

/**
 * One file's outcome of an import. A document whose semantic index failed
 * arrives twice - a `warning` entry, then the finished `ready_without_embeddings`
 * one - and `summarizeImportResults` is where the pair becomes a single row.
 */
export interface KnowledgeImportResult {
  filename: string
  /** `ready` | `ready_without_embeddings` | `warning` | `skipped` */
  status: string
  reason?: string
  document?: KnowledgeDocument
}

/**
 * One entry of the recycle bin (`GET /api/trash`). `snapshot` is the row as it
 * was when it was deleted, so a deleted memory can be shown without a way to
 * read soft-deleted rows back.
 */
export interface TrashItem {
  id: string
  entity_type: string
  entity_id: string
  snapshot: Record<string, unknown>
  deleted_at: string
  restored_at: string | null
}

export interface Health {
  status: string
  configured: boolean
  service: string
}

/** One retrieval model config as `GET /api/retrieval-settings` returns it. */
export interface RetrievalModelSetting {
  base_url: string
  model: string
  /** Whether a key is stored. The key itself is never in here. */
  has_api_key: boolean
}

/** Both retrieval configs; a kind the server has none of comes back null. */
/** One retrieval floor as the GET returns it: the value in force and the
 *  default it restores to, the shape AppSettings.tool_limits draws. */
export interface RetrievalThresholdState {
  value: number
  default: number
  is_default: boolean
}

export interface RetrievalSettings {
  embedding: RetrievalModelSetting | null
  rerank: RetrievalModelSetting | null
  thresholds: {
    memory_floor: RetrievalThresholdState
    semantic_floor: RetrievalThresholdState
    rerank_floor: RetrievalThresholdState
  }
}

/**
 * The body of a retrieval-settings PUT (`schemas.py`'s `RetrievalSettingsUpdate`).
 *
 * Same key deal as profiles: a value replaces the stored key, an empty string
 * deletes it, and leaving the field out keeps whatever is stored. A kind sent
 * as null clears that whole config; a kind left out is untouched. A threshold
 * sent as a number writes it, null restores its default; left out is untouched.
 */
export interface RetrievalSettingsPatch {
  embedding?: { base_url: string; model: string; api_key?: string | null } | null
  rerank?: { base_url: string; model: string; api_key?: string | null } | null
  thresholds?: {
    memory_floor?: number | null
    semantic_floor?: number | null
    rerank_floor?: number | null
  }
}

/** The response of the retrieval test endpoints. */
export interface RetrievalTestResult {
  ok: boolean
  /** Only the embedding test reports this: the width of one vector. */
  dimensions?: number
}

/** One installed skill as `GET /api/skills` returns it. */
export interface SkillInfo {
  name: string
  description: string | null
  enabled: boolean
  /**
   * True when the folder exists but its SKILL.md cannot be used - a missing
   * frontmatter, a name that disagrees with the folder. `error` says why, and
   * the card shows it instead of a switch that would do nothing.
   */
  broken: boolean
  error: string | null
}
