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
  content: string
  score: number
  source: string
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
  embedding_model: string | null
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
  embedding_model?: string | null
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
}

export type PromptKey = keyof AppSettings['prompts']

/** A save: the text to store, or null to go back to the built-in one. */
export type PromptPatch = Partial<Record<PromptKey, string | null>>

/** Anything the settings PUT accepts: prompt texts and the quick-prompt list. */
export type SettingsPatch = PromptPatch & { quick_prompts?: QuickPrompt[] | null }

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
