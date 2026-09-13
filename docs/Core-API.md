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
- GET /api/model-profiles/{id}/models returns that endpoint's own model list,
  read live: `{"models": [...]}`. It is what the composer's dropdown offers.

Secrets are write-only: a profile response exposes has_api_key, never an API
key.

Each profile carries two request fragments, thinking_on and thinking_off, both
JSON objects and both empty by default. **Only thinking_off still reaches the
wire.** It is merged into the provider request body *before* the fields this
service sets itself, so a fragment can describe a provider's dialect -
`{"enable_thinking": false}`, `{"reasoning_effort": "low"}`,
`{"thinking": {"type": "disabled"}}` - but can never redirect the app: model,
messages, stream and max_tokens always win. An empty fragment adds nothing,
which is not the same as telling the endpoint "no". Internal calls (compaction,
memory extraction) ask for no thinking and so carry the thinking_off fragment;
that is what "off by default" means concretely.

thinking_on is read by nobody, and sent to nobody, since `thinking` became a
dial (see Streaming chat, below). It is still stored and still returned, and an
update that carries it back unchanged rewrites it with itself, so a profile
written while thinking was a switch loses nothing - but nothing new should start
reading it without first deciding that the strengths are configurable again.

## Projects and conversations

- GET and POST /api/projects, PATCH and DELETE /api/projects/{id}
- GET and POST /api/conversations?project_id={id}
- GET, PATCH, and DELETE /api/conversations/{id}
- GET /api/conversations/{id}/messages
- POST /api/messages/{id}/regenerate

Messages are never deleted on their own; the API has no endpoint for it. A
conversation is the unit the user removes, from the conversation list. A turn
therefore cannot end up half-removed, which is what lets regenerate treat the
latest reply as always present.

POST /api/messages/{id}/regenerate re-runs the answer to the user message the
target replied to, and returns text/event-stream exactly like a new run. It
accepts optional model_profile_id, chat_model, and thinking - the same three a
new run takes. Only the newest message
in a conversation can be regenerated, since replacing an earlier answer would
orphan everything after it. The previous answer is cleared in place and the
assistant message keeps its ID, so the frontend can reuse the element it
already rendered. Both the original and the new attempt stay in the run log
for auditing.

## Streaming chat

POST /api/conversations/{id}/runs accepts content, optional model_profile_id,
optional chat_model, and optional thinking, the composer's dial position (off by
default). It returns text/event-stream. Events use a JSON data payload:

| Event | Meaning |
| --- | --- |
| run.started | Returns run and placeholder assistant-message IDs. |
| audit | Internal stage progress: compact, context retrieval, model stream, memory write. |
| context.ready | Estimated context budget and structured knowledge citations. |
| message.delta | Assistant text produced since the last delta this client received. |
| reasoning.delta | Optional compatible-provider reasoning text, same rule. |
| message.completed | Persisted final message and metadata. |
| run.cancelled / run.failed | Terminal status. |

The two choices ride on the request and are deliberately not stored anywhere.
chat_model names the model this one run should use; absent means the profile's
own, which is what every internal caller wants. thinking is where the dial
stands - one of "off", "low", "high", "max", defaulting to "off". "off" merges
the profile's thinking_off fragment. The three strengths send `reasoning_effort`
with that word and nothing else: not the profile's fragment, because the field
name and its values are this service's own, which is also why they need no
configuration. A client from before the dial sent `true`; the field deliberately
kept its name, so that value is now a 422 that names it rather than a key the
parser drops in silence. A run record therefore remembers the profile it used
and not the model name it was handed - the composer's choice is the composer's,
and a reload comes back to what the profile itself says.

Use POST /api/runs/{run_id}/cancel for the stop button. Use
POST /api/conversations/{id}/compact for an explicit compact action.

### A run outlives the connection that started it

The response is a view onto a run, not the run itself. Closing the connection -
a reload, a navigation, a browser crash - leaves the answer generating, and the
run still finishes and persists. Two routes serve a client that comes back:

- GET /api/runs/{run_id} returns the run's real status, the authority on whether
  it is still going. The assistant message carries this ID at
  metadata.run_id, so a client that reloaded can find the run it was watching.
- GET /api/runs/{run_id}/stream reattaches, whether the run is in flight or long
  finished.

Text is delivered as cumulative snapshots: each message.delta carries everything
produced since the last one *that client* received, not a fixed chunk. A client
that attaches halfway through therefore renders the whole answer in its first
flush and then continues token by token, with no gap and no duplication, and two
clients watching at once each get their own complete copy. A client that attaches
after the run ended gets run.started and the terminal event, whose content field
holds the finished answer - so one code path renders both cases.

The streaming answer is checkpointed to the database roughly every half second
or 400 characters, whichever comes first. That interval is what a power cut can
cost; everything already written survives.

### When the service itself is killed

A run is driven by an in-process task, so killing the process strands it with
nobody left to finish it. On the next startup the service closes every such run
as interrupted, with an explanation, and keeps whatever partial answer and
reasoning had been checkpointed. GET /api/runs/{run_id} reports interrupted and
GET /api/runs/{run_id}/stream reports it as run.failed, so a client that comes
back is told the answer stopped rather than left waiting on one that will never
arrive. Deciding what to do with a half-finished answer is the user's call.

GET /api/runs/{run_id}/events replays the persisted audit trail for a run, so a
frontend that reloaded mid-stream can rebuild the progress log it missed. The
audit trail is the durable record; the broadcast above is only for the live run.

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

Deleting a project, conversation, memory, model profile, or knowledge document
moves it to the trash instead of erasing it.

- GET /api/trash lists the trashed items newest first.
- POST /api/trash/{trash_id}/restore puts the item back and returns it.
- DELETE /api/trash/{trash_id} removes the entry from the trash, after which it
  can no longer be restored.

DELETE /api/trash/{trash_id} is not a physical erase. The soft-deleted row stays
in its table and its content remains in the database file, because
assistant_runs references messages by foreign key and the run log is the audit
trail. The item simply stops being reachable through the API. If you need data
actually gone from disk, that is a separate feature and does not exist yet.

