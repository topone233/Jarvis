# Jarvis Core API Contract

The frontend communicates only with the local FastAPI service, normally at
http://127.0.0.1:8787. The service is unauthenticated because it binds to the
local machine and is designed for one personal user.

## Setup and configuration

- GET /api/health returns a configured flag.
- POST /api/setup accepts a JSON object with a data_directory field.
- GET and POST /api/model-profiles, plus PATCH and DELETE
  /api/model-profiles/{id}, manage OpenAI Chat Completions compatible profiles.
- POST /api/model-profiles/{id}/test verifies GET /models.

Secrets are write-only: a profile response exposes has_api_key, never an API
key.

## Projects and conversations

- GET and POST /api/projects, PATCH and DELETE /api/projects/{id}
- GET and POST /api/conversations?project_id={id}
- GET, PATCH, and DELETE /api/conversations/{id}
- GET /api/conversations/{id}/messages
- DELETE /api/messages/{id}
- POST /api/messages/{id}/regenerate

POST /api/messages/{id}/regenerate re-runs the answer to the user message the
target replied to, and returns text/event-stream exactly like a new run. It
accepts optional model_profile_id and reasoning_level. Only the newest message
in a conversation can be regenerated, since replacing an earlier answer would
orphan everything after it. The previous answer is cleared in place and the
assistant message keeps its ID, so the frontend can reuse the element it
already rendered. Both the original and the new attempt stay in the run log
for auditing.

## Streaming chat

POST /api/conversations/{id}/runs accepts content, optional model_profile_id,
and optional reasoning_level. It returns text/event-stream. Events use a JSON
data payload:

| Event | Meaning |
| --- | --- |
| run.started | Returns run and placeholder assistant-message IDs. |
| audit | Internal stage progress: compact, context retrieval, model stream, memory write. |
| context.ready | Estimated context budget and structured knowledge citations. |
| message.delta | Incremental assistant text. |
| reasoning.delta | Optional compatible-provider reasoning text. |
| message.completed | Persisted final message and metadata. |
| run.cancelled / run.failed | Terminal status. |

Use POST /api/runs/{run_id}/cancel for the stop button. Use
POST /api/conversations/{id}/compact for an explicit compact action.

GET /api/runs/{run_id}/events replays the persisted audit trail for a run, so a
frontend that reloaded mid-stream can rebuild the progress log it missed.

## Memory and knowledge

- GET /api/memories?project_id={id}, PATCH and DELETE /api/memories/{id}
- POST /api/messages/{id}/feedback with kind equal to up or down
- POST /api/knowledge/import is multipart with files, optional project_id,
  model_profile_id, and matching relative_paths.
- GET /api/knowledge/documents?project_id={id}
- GET /api/knowledge/search?query=...&project_id=...
- DELETE /api/knowledge/documents/{id}

Only text, Markdown, and common source-code formats are accepted in this
release. Imports are copied into the user-selected Jarvis data directory; the
service never alters the original source file or folder.

## Trash

Deleting a project, conversation, message, memory, model profile, or knowledge
document moves it to the trash instead of erasing it.

- GET /api/trash lists the trashed items newest first.
- POST /api/trash/{trash_id}/restore puts the item back and returns it.
- DELETE /api/trash/{trash_id} discards the item permanently.

