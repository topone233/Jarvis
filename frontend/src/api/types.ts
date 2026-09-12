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
  is_pinned: number
  created_at: string
  updated_at: string
  deleted_at: string | null
}

export interface ModelProfile {
  id: string
  name: string
  base_url: string
  protocol: string
  chat_model: string
  embedding_model: string | null
  context_window: number
  output_token_reserve: number
  reasoning_levels: string[]
  is_default: number
  has_api_key: number
  created_at: string
  updated_at: string
  deleted_at: string | null
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

export interface Health {
  status: string
  configured: boolean
  service: string
}
