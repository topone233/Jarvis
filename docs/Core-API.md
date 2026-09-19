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

### Retrieval models

Embedding and rerank are one global config each, deliberately apart from the
chat profiles - they serve every conversation the same way, and their keys
outlive any single profile.

- GET /api/retrieval-settings returns
  `{"embedding": {base_url, model, has_api_key} | null, "rerank": {...} | null}`.
- PUT /api/retrieval-settings saves both cards at once; per kind, a kind left
  out is untouched and a kind sent as null is cleared (config and keyring entry
  both). Inside a spec, api_key is write-only with the profile form's deal: a
  value replaces the stored key, an empty string deletes it, absent or null
  leaves it alone.
- POST /api/retrieval-settings/embedding/test makes a real embed call and
  returns `{ok, dimensions}`. POST /api/retrieval-settings/rerank/test makes a
  real rerank call and returns `{ok}`. Both accept an optional body of
  base_url / model / api_key; a field left out falls back to the stored
  config, so a saved config can be re-tested without retyping the key. No
  config anywhere and no fields sent is a 422.

When rerank is configured, knowledge search sends the top 20 hybrid candidates
to the rerank endpoint (Cohere-compatible `/rerank`) and the returned
relevance_score becomes each hit's `score`, with `source: "reranked"`. A
rerank call that fails only degrades the search back to the mixed order - it
never fails the retrieval.

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

Memory is decided by the main model inside the reply itself, via native tool
calling. When the user-editable `memory_prompt` directive is present, the
request registers two tools - `save_memory` (kind/key/content/confidence) and
`forget_memory` (key/content, both copied from the injected memory list) - and
the assistant message may carry content and `tool_calls` side by side. No
`tool_choice` is set, the model decides when a turn deserves a call, and no
tool result is ever sent back: a call is an instruction to record, not a
question to answer. The producer carries the calls out once the reply is
complete and audits them as a `memory_write` stage - which exists **only when
the calls produced actions**. A plain answer has no memory step at all, no
extra model call, and text that streams is shown whole; a call whose arguments
do not parse leaves no trace. A cleared `memory_prompt` registers no tools,
which is what "the model does not manage memory" means on the wire.

Injection is selective. Candidates are the 16 most recent active memories;
first a dedup pass drops any whose source message (recorded on the memory) is
still visible in this turn's window or has been folded into the compaction
artifact — the information is already in front of the model, so re-injecting
it is duplication. When a retrieval embedding model is configured (see
Retrieval models), the rest
are ranked against the query vector (cosine, floor 0.25, at most 6) and only
relevant ones are injected; vectors are cached on the memory row and refreshed
lazily, one batched embed call per turn, shared with knowledge search. Without
an embedding model, on a blank (image-only) query, or when the embed call
fails, the deduped list is injected unranked — never worse than a plain dump.
A query that means to forget (contains 忘/forget) lists everything, because
forget entries must be copied from the `<memory>` list. The `context_retrieval`
audit event reports the outcome as `memory_mode`: `relevance`, `forget_bypass`,
or `fallback`.

- GET /api/memories?project_id={id}, PATCH and DELETE /api/memories/{id}. The
  API never returns the cached embedding columns; they are retrieval plumbing.
- POST /api/messages/{id}/feedback with kind equal to up or down
- POST /api/knowledge/import is multipart with files, optional project_id, and
  matching relative_paths. Whether chunks get vectors depends on the retrieval
  embedding config, not on any profile.
- GET /api/knowledge/search?query=...&project_id=... Each hit carries score
  (the reranker's relevance_score when rerank produced the order) and source
  (`reranked`, `hybrid`, or `semantic`).
- DELETE /api/knowledge/documents/{id}

DELETE /api/memories/{id} soft-deletes: the row goes to the recycle bin and can
be restored there (POST /api/trash/{trash_id}/restore). The frontend's memory
page has a deleted section for exactly that, plus a permanent delete, which is
DELETE /api/trash/{trash_id} on the bin entry.

Only text, Markdown, common source-code formats, and docx/xlsx/pptx/pdf are
accepted in this release. The 97-2003 binary formats (.doc/.xls/.ppt) are
rejected with a message asking for a resave; a scanned PDF extracts no text and
is skipped. Imports are copied into the user-selected Jarvis data directory;
the service never alters the original source file or folder. The document and
retrieval design behind these endpoints is docs/RAG.md.

## Trash

Deleting a memory, model profile, or knowledge document moves it to the trash
instead of erasing it. **Deleting a conversation erases it**, permanently and
physically: messages, the run log, audit events, compaction records, feedback,
and any bin entries that pointed into it are deleted for real - a decision the
user made on 2026-09-14, replacing the earlier recycle-bin semantics for
conversations. Memories referenced by those messages lose their
source-message pointer, nothing else.

- GET /api/trash lists the trashed items newest first.
- POST /api/trash/{trash_id}/restore puts the item back and returns it.
- DELETE /api/trash/{trash_id} removes the entry from the trash, after which it
  can no longer be restored.

DELETE /api/trash/{trash_id} is not a physical erase of the row it names - the
soft-deleted row stays in its table so a restore is possible, and for messages
because assistant_runs references them by foreign key. If you need data
actually gone from disk, delete the conversation it belongs to, or discard the
bin entry and drop the database file.

