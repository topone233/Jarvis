# Engineering Notes

Lessons that cost real debugging time here. Each entry records the symptom, the
actual root cause, and the invariant that keeps it from coming back. Add to this
file whenever a bug's cause was not obvious from the code.

## A 204 route needs `response_model=None` under PEP 563

**Symptom.** The whole app failed to import: `AssertionError: Status code 204
must not have a response body`, raised inside FastAPI while `create_app()` ran.
No test could even be collected.

**Root cause.** Every module starts with `from __future__ import annotations`, so
an annotation is a *string* until something resolves it. FastAPI resolves the
return annotation and gets `NoneType` for `-> None`; `NoneType` is truthy, so
FastAPI treats it as a declared `response_model` and asserts that a 204 response
cannot carry a body. The annotation is innocent — FastAPI is reading a truthy
class object where it expected an absence.

**Invariant.** Any route that returns no body declares `response_model=None`
alongside `status_code=204`. The codebase has six of them; copy the pattern.

## FTS5 cannot see inside a CJK run without help

**Symptom.** Chinese knowledge-base search silently returned nothing. Searching
`上下文` never matched a document containing `并支持上下文压缩`, while `SQLite`
matched fine.

**Root cause.** The default `unicode61` tokenizer splits on script boundaries and
whitespace, so an entire uninterrupted CJK run becomes a *single* token. Sub-
phrase search is impossible against it.

**Why bigrams.** Three options were measured against the same corpus:

| tokenizer | `上下文` | `压缩` | `记忆` | `SQLite` |
| --- | --- | --- | --- | --- |
| `unicode61` raw | miss | miss | miss | hit |
| `unicode61` + bigram expansion | hit | hit | miss | hit |
| `trigram` | hit | **miss** | miss | hit |

`trigram` needs three characters, so it fails on two-character queries, which are
the common case in Chinese. Overlapping bigrams handle both lengths and need no
new dependency.

**Invariant.** Indexing and querying must both pass text through
`app.utils.segment_for_index`. If you change the tokenizer, bump
`Database.SCHEMA_VERSION` so `_apply_migrations` rebuilds `knowledge_chunks_fts`
from `knowledge_chunks` — the FTS table is derived data and an already-migrated
database will otherwise keep its stale index.

## A "silent no-op" edit was escape normalization, not a sandbox

**Symptom.** A script rewrote one line of `app/utils.py`, reported success, and
the file was byte-for-byte unchanged.

**Root cause.** The intended replacement was a `一`-style escape sequence
and the original was already the literal character `一`. Both spell the same
string, so the write succeeded and produced identical content. The Edit tool
reports the same condition as "old_string and new_string are identical".

**Correction.** I first blamed the Bash sandbox for dropping writes and reported
that. It was wrong: a direct test writing a file from Python launched through
Bash showed the change landing on disk. Writes persist; the content was the
same. Do not conclude "the tool ate my write" without diffing the result.

**Invariant.** When a write appears to vanish, compare the before and after
bytes before blaming the toolchain. Prefer the literal character over an escape
in regex ranges, and comment the intended code point instead.

## Derived state needs a migration, not just a version bump

`SCHEMA_VERSION` was decorative. `executescript(SCHEMA_SQL)` uses
`CREATE TABLE IF NOT EXISTS`, so an existing database silently kept old columns,
old indexes, and old derived rows while the code assumed the new shape.

**Invariant.** Adding or changing anything that already exists in a released
database means adding a branch to `_apply_migrations` keyed on the recorded
version. Changing only `SCHEMA_SQL` upgrades fresh installs and nothing else.

## `Database.transaction()` does not nest

It opens a fresh connection, commits, and closes it — no savepoints, no
re-entrancy. Two calls to a `_soft_delete`-style helper therefore run as two
independent transactions: a failure between them commits half the work, and the
second connection can fight the first for the write lock.

**Invariant.** Anything that must land together goes inside one
`with self.database.transaction() as connection:` block. Do not compose two
helpers that each open their own — hand the helper every row it has to write, or
open the transaction in the caller. Nothing in the codebase needs this today;
it matters the moment a write touches two tables and must be all-or-nothing.

## A name can promise more than the code does

**Symptom.** `permanently_delete_trash_item` read like the one place that erases
data. It deleted the `trash_items` row and nothing else; the entity row stayed in
its table with its content intact, so a "permanently deleted" message was still
readable in the database file.

**Root cause.** The recycle bin is soft deletion end to end, and physical
deletion was never built. The name described an intent no code carried out.

**Resolution.** Renamed to `discard_trash_item` and documented the real
behavior. Erasing rows for real is a feature with sharp edges — it would have to
clear `assistant_runs` and `run_events`, which reference `messages` by foreign
key, and it would destroy the audit trail. Not wanted yet, so the behavior
stayed and the words were corrected.

**Invariant.** Name a function for what it does to the data, not for what the
user believes is happening. When those diverge, fix the name or fix the
behavior. Leaving the gap unstated is how a privacy expectation quietly breaks.

## A long task must not be owned by the HTTP response that started it

**Symptom.** Reloading the page while the model was still answering threw the
answer away. The run stayed in the database as `running` forever, with a partial
or empty message.

**Investigation.** Not a frontend bug, and not a provider bug — the request was
being aborted. uvicorn 0.34.0 advertises ASGI `spec_version: "2.3"`
(`uvicorn/protocols/http/h11_impl.py:203`), and starlette 0.46.2's
`StreamingResponse.__call__` takes the `spec_version < (2, 4)` branch for that
version: it runs the body inside a task group alongside `listen_for_disconnect`
and calls `task_group.cancel_scope.cancel()` on `http.disconnect`
(`starlette/responses.py:253-270`). The generator driving the run got a
`GeneratorExit`, and the provider request went with it.

**Root cause.** The run had exactly one driver — the response body generator —
so the run was owned by the connection. Any network hiccup, reload, or closed
tab was a kill signal. Nothing in the code said the answer should depend on a
browser staying open; the architecture said it by accident.

**Resolution.** The producer became a task of its own (`RunService.launch`)
publishing into a `RunBroadcast`, and the response became a subscriber to it
(`RunService.follow`). Disconnecting now costs nothing. Reconnecting is free
because text is published as a cumulative snapshot rather than a queue of
deltas: a subscriber only tracks how much it has already sent, so joining late
converges instead of needing the backlog replayed to it. The run also checkpoints
to the database every half second or 400 characters, and startup closes any run
a previous process left stranded.

**The harness had the same bug.** `TestClient` builds and tears down an anyio
portal per request (`starlette/testclient.py:335`) unless it is used as a context
manager (`__enter__`, line 661). With the fixture returning a bare
`TestClient(...)`, every detached task was destroyed when its request ended —
the tests reproduced the production bug in miniature. The fixture now enters the
client. A test double that quietly kills background work is worse than no test,
because it makes the new architecture look broken when it is only the harness.

**Invariant.** Work that must outlive a request does not belong in that
request's response body. The response is a view onto the work; if cancelling the
view cancels the work, the two are the same object and that is the bug. Concretely
here: a client that closes its connection should not be able to reach the
generator, the task, or the provider call.

## Headers built in two places silently lose the ones only one of them sets

**Symptom.** Every message sent from the browser answered 500 and produced no
answer at all. The backend log said `TypeError: Object of type bytes is not JSON
serializable`, raised while handling a request nobody had rejected.

**Investigation.** Three layers down from where it looked. The send path built
its request headers in `streamRun.ts` by spreading `init.headers` over a freshly
built object; both call sites passed no headers, so the spread quietly removed
`Content-Type: application/json`. A `fetch` body that arrives without it is *not
parsed as JSON at all* — FastAPI hands the raw bytes to the validator. The
validator rejects them, which is correct, and then
`RequestValidationError.errors()` echoes the offending input, now a `bytes`, into
a response body `json.dumps` cannot serialise. The 422 became a 500 that
described none of this.

**Root cause.** Two code paths constructed one request's headers, so the second
one's omissions were invisible at the first one's call site. The downstream crash
was a serialization detail standing in for a missing header.

**Resolution.** `withJsonBody` in `api/client.ts` builds the headers and is the
only thing that does: a body implies the content type, an explicit `accept`
argument implies the accept header, and the streaming call site composes it
instead of rebuilding it. The validation handler encodes its `detail` with
`jsonable_encoder`, so echoing a non-JSON input can never turn a 422 into a 500.
Tests assert the headers on both kinds of call — with a body and without — and
were confirmed to fail against the old code.

**Invariant.** One request's headers come from one place. And an error handler
that echoes its input must be able to serialise what it echoes, or the error that
explains the problem becomes a second, louder error that does not.

## State that means "something is queued" must be cleared when the queue is dropped

**Symptom.** Sending the first message of a brand-new conversation showed nothing
at all. The network log said `POST /api/conversations/{id}/runs` had been
cancelled before it left the browser.

**Investigation.** Two defects in one `useEffect` cleanup, the second hidden
behind the first. React StrictMode mounts, cleans up, and mounts again in
development, so that cleanup runs once immediately after every mount. It was
aborting the in-flight POST — at that instant the POST had been issued but
`run.started` had not come back, so the run id was still empty and the code had
nothing to tell "the request I am watching" from "a request I have not been told
about yet". Aborting there kills the first message of every new conversation.
Behind it: the same cleanup cancelled the animation frame but left
`frameRef.current` holding the cancelled id, and `commit` reads a non-null id as
"a flush is already queued" and returns without scheduling another one — so every
later update would have been dropped and the answer would never have appeared
even with the abort fixed.

**Root cause.** Both halves come from treating a cleanup as "undo the mount"
rather than "release what this effect is holding". One held a connection whose
identity was not yet known; the other held a flag with no owner left to clear it.

**Resolution.** Abort only when there is a run id to abort — a send the server
has not acknowledged is not ours to recall, and leaving it alone is what lets the
latch that stops double-sending work. Clear the frame id alongside cancelling it.
The load effect also refuses to reattach over a turn this page is already driving:
otherwise the history re-read and the send both answer for the same run, and the
second takes over the stream that was already delivering the answer.

**Invariant.** A cleanup releases what its own effect took, and nothing more. Any
ref that means "already scheduled" has an owner that clears it on every path out.

## A page that fails to load must not look like a page with nothing in it

**Symptom.** Opening a conversation that was not in the current data directory
rendered an empty screen: no history, no error, no empty state. Indistinguishable
from a conversation with no messages, and from a page that had not finished
loading.

**Root cause.** The load effect's `listMessages` promise was never given a
handler, so its rejection went nowhere and `messages` simply stayed `[]`. The
failure was reported to the console of whoever happened to have devtools open.

**Resolution.** The load has its own `catch`, and a rejected load sets
`loadError`, which renders a panel naming the failure and saying where the
conversations in the sidebar actually live. The error does not replace the
conversation; it explains the absence of one.

**Invariant.** "There is nothing here" and "I could not find out what is here"
are different facts and need different words. If the only evidence of a failure
is a console line, the user is being asked to open devtools to find out that the
app is broken.

## A screen that can change which data set is in use has to say which one is

**Symptom.** Set the app up in one browser, open it in another, and the
configuration and the conversation list appeared to be gone. Returning to the
settings screen and pressing 下一步 made them disappear for real.

**Investigation.** The app was fine: one backend, one data directory, both
browsers reading the same rows. The settings screen's directory field started
*empty* on every visit, so it showed a question where the answer was already
known — and its next action pointed the whole app at whatever the field held.
Untouched and empty, that is the current directory. Type anything else and press
next, and the conversations stay where they were while the app starts looking
somewhere else for them.

**Root cause.** The field was treated as an input when it is also a report. Only
the "change it" reading was built, so "which one is it now" had no representation
on screen and no way to be wrong out loud.

**Resolution.** The page reads `GET /api/health` for `data_directory`, fills the
box with it while the box is untouched, and always shows 当前使用的是 … above it.
The field's keypress handler was also calling the wrong function. Changing the
directory for real reloads the page, because every list on screen belongs to the
old one and leaving stale rows up would misrepresent what just happened.

**The same screen had a second, related defect.** The connection test ran after
saving the model profile, and entering the app happened at that same moment, so
the screen unmounted before the result arrived and the test's answer — the only
moment where a wrong address or a bad key can still be corrected on the spot —
was never seen.

**Invariant.** When a control both reports and changes state, the report has to
be on screen before the change can be made. And a step whose output is only
useful if the user is still there must not exit on its own.

## The harness lies before the code does

**Symptom.** A check reported that a page came back blank after leaving and
returning (`17 chars`), and another reported that a stream had closed on its own
after `48 bytes`. Both looked like the bugs under investigation. Neither was.

**Investigation.** `17` was the length of the string `(no message list)` — the
harness had navigated to `//c/{id}`, because it pasted a path onto a base URL
that already ended in a slash. That matches no route, falls through to the
catch-all, and renders the opening screen: exactly what a conversation that
failed to load looks like. The `48 bytes` was the backend correctly rejecting a
400 — Chinese text in a `curl -d` argument was mangled by the Windows shell, so
the body was not valid UTF-8 and the request never became an SSE stream at all.

Two more, from the same file: a wait whose condition was `x === null || true`
could never fail and so tested nothing, and reading `requestWillBeSent` alone
cannot tell "the server answered" from "cancelled before it left" — the two are
identical until the request is followed through to `loadingFinished` or
`loadingFailed`.

**Root cause.** The harness was written to produce evidence, and a check that
cannot fail produces evidence-shaped output instead. Its own failure modes were
invisible: a mangled URL and a failed request both arrive as plausible data.

**Resolution.** Paths are joined through one helper. Bodies are ASCII, or written
by something that handles encoding. Comparisons are made against the *answers* on
the page rather than the whole message list, so the progress strip a live run
carries cannot be mistaken for a difference in content. Each flow prints the
requests it made, so a request that never happened is visible next to the result
it is supposed to explain.

**Invariant.** A verification result is only as good as the harness's ability to
fail. Before believing a surprising reading, ask whether the instrument could
have produced it — and when a harness and the code disagree, find out which one
is wrong before changing either.

**And the same trap in a test.** The heartbeat test below replaces
`HEARTBEAT_SECONDS` with a monkeypatch, and the first time I checked it I edited
that constant in the source — which the test overrides, so the run reported
`1 passed` and proved nothing at all. Removing the `yield` instead, the mechanism
rather than the number, failed the test as it should. When a check passes, name
the change that would have made it fail; if the check contains that change, it
is testing itself.

## A connection that dies without saying so needs a clock, not a protocol

**Symptom.** Killing the process behind a streaming answer left the page waiting
forever. The text stopped growing, and nothing said why: no error, no notice, no
end — a person watching cannot tell a dead backend from a slow model, and the
app's central promise (the answer survives the page) rests on the client knowing
the difference.

**Investigation.** Measured twice, because the first measurement blamed the
wrong layer. Through Vite's dev proxy, a killed backend left the reader asleep
(`still waiting`) 15 seconds on; straight at the backend, the same kill threw
`terminated` in milliseconds. The difference is the proxy, but the proxy is not
the bug: neither connection ever carried a FIN. A process killed with
`Stop-Process -Force`, a machine that loses power, a socket a proxy holds open
instead of closing — none of them send anything, and the browser is told
nothing. Only an orderly close is detectable from the transport.

**Root cause.** Liveness was being inferred from the transport, which does not
carry it. Every layer in between also treats silence as normal, and rightly so:
an SSE stream is *supposed* to be quiet while a model thinks, and a proxy that
closed idle connections would break every slow answer. The app had a full
reconnect story — probe the run, reattach, say what happened — that nothing
could ever start.

**Resolution.** Two halves, useless apart. The server sends a `: keep-alive`
comment every ten seconds while a run has nothing to report
(`HEARTBEAT_SECONDS`, `RunService.follow`); a comment is ignored by every SSE
parser including this app's, which is what makes it a liveness signal that costs
the client nothing. The client races each read against a 25-second timer
(`IDLE_LIMIT_MS`, `streamRun`) and treats a timeout as a broken connection,
which lands in the retry ladder that already existed. Confirmed on the wire with
a stub told to take 12 seconds per token: the comments arrived through the Vite
proxy at 10.4s and 22.4s, and the longest silence between reads fell from the
model's 12s to 10s. The watchdog test was checked by deleting the `yield` (it
fails), and the browser flow by killing the backend mid-answer — the page now
reaches 「这一轮在生成过程中被中断了」 with the partial text intact.

**Invariant.** An interruption the client cannot see is the normal case, not an
edge case. Anything that waits on a stream must be able to answer "how long
since the last byte, and how long is too long" without asking the transport.

## Two copies of one message, both correct, rendered twice

**Symptom.** In development only: the first message of a new conversation sat on
screen twice while the answer was still being written. Reload, and there was one.

**Investigation.** React StrictMode mounts, cleans up, and mounts again, so the
history load runs twice. By the time the second run's answer came back, the send
had already been stored, so the loaded history contained the user's message — and
`pendingUser`, the optimistic bubble shown while the POST is in flight, was still
set. Two pieces of state, each accurate about the same message, both rendered.

**Root cause.** The optimistic copy had no rule for when to stand down. From its
point of view nothing had changed: the request it was standing in for was still
running, and it clears on the terminal refresh — which is the right moment for
the *turn*, and minutes too late for the message.

**Resolution.** The load drops it: `alreadyStored(loaded, pending)` clears the
bubble when the history it just read contains a user message with that text. Text
is safe to compare *here* and only here — the load effect runs at mount and when
the conversation changes, never while a conversation is in use, so a match can
only be the message just sent. Reproduced and confirmed by capturing the DOM
mid-answer (a stub slowed to 12 seconds per token, read at 7s): two `msg-user`
rows before, one after.

**Invariant.** When two pieces of state can represent the same fact, one of them
has to lose at the moment the other arrives. "Cleared at the end of the
operation" is not a rule, it is a delay — and the operation may be minutes long.

## Configuration is state; a wizard is what it looks like the first time

**Symptom.** A model profile was configured, and opening the settings screen
again showed an empty form. Saving it again made a *second* profile rather than
editing the one that was there. The same screen also insisted on its two steps —
data directory, then model — on every visit, long after both answers were known.

**Investigation.** The page had exactly one shape: create. It never called
`GET /api/model-profiles`, so the only thing the form could show was its initial
state, and that was always blank. The step was a `useState(1)` that only the step
one button ever advanced, so every visit began at the beginning. Nothing was
lost — the profile was in the database the whole time, and the API had been
returning it — the screen simply never asked.

**Root cause.** The page modelled configuration as an *event*: a wizard that runs
once and is then over. What it manages is *state* — which directory is in use,
which profiles exist, which one is the default. A create-only, write-only form
has no memory and cannot be wrong out loud, so on a second visit it is
necessarily empty and every save necessarily adds a row. The two steps were a
real constraint once — profiles are rows inside the data directory's database, so
until a directory has been chosen there is nowhere to put one and every other
route answers `409 setup_required` — but it is a constraint of the *first* run,
and the page was applying it forever.

**Resolution.** The screen has two shapes, and which one it is comes from
outside: `App` already reads `/api/health` for its gate, so it passes
`configured` in rather than letting the page re-derive a second answer. Not
configured: the same two steps as before, because the order is a data dependency.
Configured: one page, two independent sections, no steps; the directory in use is
shown and its button refuses to act while the box still names it; the profiles
are listed with the one in effect marked, and each can be opened, edited, made
the default or deleted in any order. Deleting the default is allowed and promotes
nobody, so the screen names the profile a run will fall back to instead of
leaving a missing badge unexplained.

**A second defect in the same shape, and the one that could have destroyed
something.** The key field is necessarily empty when an existing profile is
opened: the API never returns the key — it lives in the OS credential manager and
`has_api_key` is all the server will say. `PATCH` reads an explicit
`api_key: ""` as *delete the stored key* (`main.py` maps a falsy value to
`secrets.delete`). A form that sends every field it holds would therefore wipe the
key of a profile the user only wanted to rename, silently, on a successful
request. The rule is that an untouched box is left *out* of the body rather than
sent empty; it lives in one pure function with its own tests
(`frontend/src/api/profiles.ts`), and it was confirmed against a real key rather
than by reading the code: a key was put on a profile through the API, the profile
was renamed in the UI, and the API was asked again — still there.

**Invariant.** A screen that edits something must read it first. If a form can
only create, every visit is a first visit, and the user's second click makes a
duplicate of the thing they came to change. And where a field is empty because it
*cannot* be shown rather than because it is empty, absent and empty are different
requests.

## A hand-written mirror has to be copied from a response, not from the schema

**Symptom.** None. That is the point: the context ring had no total on a new
conversation, so it drew nothing, and nothing errored, logged, or failed a test.

**Investigation.** The frontend declares its own types (`frontend/src/api/types.ts`,
hand-written on purpose — the surface is small and a generator plus its config
would be more machinery than the drift it prevents). `ModelProfile.is_default`
was declared `number` and compared with `=== 1`. The API returns
`"is_default": true`. `true === 1` is false, so the fallback never fired — and a
comparison against a wrong type cannot throw, so it simply never happened.

**Root cause.** The types were transcribed from the *columns*.
`backend/app/database.py` declares `is_default INTEGER NOT NULL`, which is true of
the table and wrong about the wire: `store.py` passes `is_default`, `has_api_key`
and `is_pinned` through `_record(..., bool_fields=(...))`, which runs `bool(...)`
over them on the way out. Two layers, two truthful statements, and the mirror was
written from the wrong one.

**Resolution.** The three fields are declared `boolean`, with the reason in a
comment above `ModelProfile` so the next reader does not re-derive it from the
table again. It was found by printing an actual response — one `curl` of the
route that already existed — which is what would have prevented it. The new
settings code had inherited the wrong type from the old mirror, so the same
`=== 1` was about to be written a second time.

**Invariant.** A hand-written type is a claim about a response, so write it from a
response that was actually printed. A schema and a response disagree exactly where
a conversion sits between them — and the mirror only ever describes the far side.

## `window.confirm` stops the page, and its replacement has two ways to never answer

**Symptom.** Asking "move this conversation to the trash?" froze the tab. The
stream behind it stopped arriving, and the answer being written was cut off.

**Root cause.** That is what a native dialog *is*: the browser suspends the
event loop in the tab until somebody clicks. Nothing in this app can survive it —
a stream is a sequence of reads that only happen when the loop runs. So the
complaint "not modern-looking" was really about a modal that stops the world, and
the fix has to be a modal that does not.

**Resolution.** A `<dialog>` opened with `showModal()` gives the parts that are
easy to get wrong for free: the top layer, `::backdrop`, a real focus trap, and
Escape. It also creates two ways for the promise to be abandoned while the caller
still believes a question is on screen, and both were handled explicitly:

- **Escape.** A modal `<dialog>` closes itself on Escape without any handler of
  ours running. The `cancel` event has to `preventDefault()` and settle the
  promise; otherwise the element is gone, the state still says a question is
  open, and the `await` never returns.
- **A backdrop click is delivered to the `<dialog>` element itself**, not to the
  page behind it and not to a child. So `event.target === dialog.current` is the
  test for "outside the box" — but only if the box inside does not fill the
  element. The padded area belongs to `.confirm-box`; give the dialog the padding
  instead and a click on the padding reads as the backdrop.

**Invariant.** Anything that replaces a native modal takes over the native ways
of dismissing it. For every path that closes the dialog — button, Escape,
backdrop, unmount — name the code that settles the promise, and check that state
and screen agree afterwards.

## JSX attribute strings are literal, not JavaScript strings

**Symptom.** A path placeholder read `D:\\JarvisData` in the browser. The source
said `placeholder="D:\\JarvisData"`, which in JavaScript is one backslash and
looks correct in review — and the rendering was right.

**Root cause.** A JSX attribute written with quotes is an HTML attribute, not a
JavaScript string. No escape processing happens: `"D:\\JarvisData"` is literally
two backslashes, and Windows accepts the doubled form in most places, which is
why it had gone unnoticed. In `{'...'}` (braces) it *is* a JavaScript string and
the escape does apply.

**Resolution.** Fixed to `placeholder="D:\JarvisData"`. Confirmed by emitting the
JSX and reading the runtime value, because the frontend has no DOM test
environment — `vitest` here runs in `node`, so a rendering question cannot be
answered by a unit test and was not.

**Invariant.** Inside a quoted JSX attribute, write the characters you want on
screen. When a rendering question needs proving and there is no DOM in the test
runner, compile the one expression and read the output rather than reasoning
about it — the escape rules are the one thing review reliably gets backwards.

## A box that looks like a window has to close like one

**Symptom.** The settings screen and the confirmation dialog both shipped without
a close button in the corner. Neither was *stuck* — the dialog had 取消 and the
settings screen had the sidebar — but both are bordered, rounded, shadowed boxes
floating over the app, and a person looking at one looks for the × first.

**Root cause.** Both were built from what they had to *do* (ask a question; edit
configuration) and not from what they had to *look like*. A card or a modal
carries a convention that appears in none of its requirements: the corner is
where you leave. Nothing in the code was wrong, and no test could have caught it
— the inventory of "what does this screen show and do" was missing the question
"what does this screen look like it is".

**Resolution.** The × is on the settings card when the caller can say where to go
back to, and absent on the first run, where there is nowhere else to be and a
way out would strand someone with no data directory. It returns to the last page
that was not the settings screen, tracked in the shell rather than read from
browser history — opening the app straight onto `/setup` leaves no earlier page
*inside* the app, and `navigate(-1)` would leave it entirely. On the dialog it
settles `false`, the same answer as 取消, and deliberately does not take focus, so
the keyboard still lands on the action.

**Invariant.** For anything drawn as a window — card, modal, drawer — say where
its × goes and whose job finding it is. "There is another way out" is not an
answer to "how do I close this".

## A second implementation of a wire format is where the errors hide

**Symptom.** A verification script reported that a run streamed no answer and
never completed. The product was fine — the same run, watched in a browser, was
correct.

**Root cause.** The script carried its own SSE parser, a third one after the
server's and the client's. It read the `data:` lines and looked for
`event["type"]` inside the payload. The frames are `event: <name>\ndata:
<payload>` (`backend/app/runs.py:30`), so the name was never in the JSON and
every frame was classified as unknown — and because it only read `data:` lines it
kept each payload attached to whatever name it had seen last, which turned an
unknown frame into a silently misattributed one.

**Investigation, and the same shape question again once the parser worked.** One
chat turn makes *two* calls to the provider: the streaming completion, and the
memory extraction moments later. Both carry the user's message verbatim, so
"the newest request containing my text" answers a question about the wrong call.
What separates them is `stream: true` versus `false`. That is only visible by
dumping every request and reading them, which is why the recorder appends to a
log instead of overwriting one file.

**Invariant.** When a checker speaks a protocol the code already speaks, take the
shape from the code that *writes* it — the frame format and the discriminator
both — and never from what seems reasonable. This is the same failure as "the
harness lies before the code does" wearing different clothes: an empty result
from a checker that re-derived the format is not evidence about the code. And
when one user action produces several requests, the one you mean is identified by
its *shape*, not by the text it happens to share with the others.

## A card taller than its window is unreachable at the top, not just the bottom

**Symptom.** Once the model form grew its call parameters, the settings card
could outgrow the window. No assertion failed: every field was in the DOM, every
value was right, every click landed. The screenshot showed it — the card's title
was cut off, and scrolling did not bring it back.

**Root cause.** `.setup` was `min-height: 100%` with `align-items: center` for
the card, inside `.app { overflow: hidden }`. A flex container with `min-height`
but no `height` grows with its content, so the card was centered in a box taller
than the window, and the top of that box sat outside the clip. With the ancestor
clipping, there is no scrollbar to reach it: content above the fold is not far
away, it is not there at all as far as the pointer is concerned.

**Resolution.** `.setup` became `height: 100%; min-height: 0; overflow-y: auto`,
and `margin: auto` on the card does the centering now that the container no
longer does. Auto margins center within the box that exists and fall to zero when
the content overflows — centered when it fits, scrollable from the very top when
it does not, which `align-items: center` cannot give. Two probe checks pin it:
the container scrolls, and the title sits within the visible area after scrolling
to the top.

**Invariant.** Centering means centering in the *visible* box, and an element's
position is not in the DOM — so no assertion about the DOM can find this class of
bug, and the screenshot is part of the harness rather than a nicety. When a
panel's content can outgrow the window, the panel owns the scroll and the card
owns the auto margins.

## Changing what crosses the wire does not restart the process serving the old one

**Symptom.** The real app on 8787 came up as a blank white page after a refresh,
with one line in the console: `Uncaught TypeError: Cannot convert undefined or
null to object at Object.keys`, raised inside a minified bundle. Nothing in the
working tree was wrong — the same code passed its gate, and a scratch backend on
8791 served the same freshly built frontend without complaint.

**Root cause.** Two things, the second hidden behind the first. The backend
process on 8787 was started at 14:16; the files it needed in order to answer
correctly (`settings.py`, then `main.py`, `provider.py`, `database.py`) were
written at 15:31 and 17:59. It was still answering with the old `model_profiles`
shape — `reasoning_levels_json`, no `thinking_on` / `thinking_off` /
`compact_percent` / `max_tokens` — while the bundle asked for
`Object.keys(profile.thinking_on)`. `undefined` is not an object. And because
that throw was in render with no boundary above it, React unmounted the entire
tree: the symptom was "the page is blank", which reads as a frontend problem and
points away from the process nobody restarted. The database was the tell — a
process running the current code migrates on startup, and this one had not, so
the file still had `reasoning_levels_json` in it.

**Resolution.** Restart the backend. Done in this order, because the migration
touches a real database that had never been through it: copy the file with
`sqlite3.Connection.backup` (not a file copy — the database is in WAL, so a
copy can miss committed pages), run the migration against the copy, and compare
column sets and row counts before touching the original. The counts matched, the
`reasoning_levels_json` column went, `integrity_check` said `ok`. After the
restart the three screens whose render path reads the changed shape came up
clean with an empty console.

**Invariant.** A frontend and a backend built from one working tree are still
two deployments, and nothing in either one notices when they drift apart. Two
consequences worth carrying forward. First, a build output served by the same
process that answers the API means every `npm run build` silently upgrades one
half of a pair — so when a change lands on the wire, say which process has to be
restarted, in the same breath, and do not wait to be asked. Second, "the page is
blank" is a symptom any component can produce, because a throw in render does
not degrade one screen, it takes the whole app; read it as "something threw"
rather than "the page is broken". This project has no error boundary and the
user chose not to add one — the console remains the only place a render throw
shows up.

## `Counter.update` counts a mapping's values, not its keys

**Symptom.** Every PDF import came back `skipped` — extraction raised
`TypeError: unsupported operand type(s) for +: 'NoneType' and 'NoneType'` —
after the page-furniture counter was rewritten from an explicit `for` loop to
what looked like an equivalent one-liner.

**Root cause.** `Counter.update()` is polymorphic: an iterable counts its
elements, but a *mapping* treats its values as counts. `dict.fromkeys(edge)`
produces `{line: None}` — deduplicated, but with `None` for every count — so
the Counter tried to add `None + None`. The deduplication that motivated the
rewrite was correct; the container was wrong. `set(edge)` deduplicates the same
way and updates as an iterable.

**Invariant.** When the argument to `Counter.update` changes shape between an
iterable and a mapping, the semantics change with it. Deduplicate with `set()`
when the goal is "once per page/occurrence", and let a test with a short page
(one whose first and last edge lines coincide) pin the once-only behavior —
that is the exact case the old code had a comment for and the new code broke.

## Narrowing does not survive capture: a lambda reads the declared type

**Symptom.** `mypy` flagged `knowledge_tool.execute(command)` inside a lambda
as `Any | None` after `str`, while the *identical* pattern in the bash branch
three screenfuls below passed.

**Root cause.** mypy applies narrowing from the enclosing scope inside a
closure only while the captured variable is not reassigned anywhere *after*
the closure is created. `command` is reassigned by each sibling tool branch
below, so the knowledge and skill lambdas (which have siblings after them)
read the declared `Any | None`; the bash branch is the last one, nothing
reassigns after it, and its lambda is trusted. The asymmetry is real, and
"but the bash one compiles" is the trap.

**Invariant.** A narrowed value crossing a function boundary must be bound to
a name whose type does not depend on narrowing — a parameter, a typed local,
or the callable's own arguments. Here `run_tool` changed from taking a
zero-arg closure to taking the bound method plus `*args, **kwargs`, so the
narrowed `command` is passed at statement level, where narrowing always
applies. Side benefit: no lambda indirection at all.

## `str(exception)` can be empty: uvicorn's reload loop cannot spawn subprocesses

**Symptom.** Every `bash` call failed as `bash: 执行异常：` — nothing after the
colon, in the audit row, in the model's tool result, and (since nothing logged)
nothing on the console either. `echo test` failed the same way as anything
complex. Every failure landed exactly one grace window after the `running`
record, meaning the subprocess never got as far as running a command.

**Root cause.** A chain of three things, each hiding the next:

1. `uvicorn --reload` on Windows makes uvicorn install
   `WindowsSelectorEventLoopPolicy` (`use_subprocess = reload or workers > 1`).
2. The **selector** loop cannot spawn subprocesses at all, so
   `asyncio.create_subprocess_exec` raises `NotImplementedError` — and it
   raises it *bare*, with no message, so `str(error)` is `""`.
3. The failure text was built as `f"{name}: 执行异常：{error}"` and nothing
   logged the exception, so the one part of the message that could have
   identified the problem (the type name, the traceback) was thrown away at
   the only two places anyone would look.

The same construction existed in the skills runner, so both tools were dead
under `--reload` while the test suite (plain `asyncio.run`, Proactor loop) was
green.

**Fix.** Both runners now drive the child synchronously on a worker thread
(`asyncio.to_thread` + `subprocess.Popen`), which is loop-agnostic — one code
path for dev, production, and tests. Failure text carries
`type(error).__name__`, `logger.exception` goes to the console, and the
traceback rides the failed audit row so a reload shows it too.

**Invariant.** Never interpolate only `str(error)` into a user-facing
failure — include the exception type name, and log with the traceback.
And a tool that spawns subprocesses must not assume the event loop can:
either use the thread path, or assert the loop kind at startup and fail
loudly, not silently at spawn time.

## Overflow clips at the padding box, so a line-count clamp must not own its padding

**Symptom.** The approval card folds a long bash command to three lines. It
showed three lines and then the top half of the fourth, right above the fold
bar. Nothing in the DOM disagreed with anything else: the `pre` was 71.8px
tall, `clientHeight` 72, `scrollHeight` 332, every number exactly as intended.

**Root cause.** `overflow: hidden` clips at the *padding box*, and
`box-sizing: border-box` means `max-height` sizes that same box. A ceiling of
`3 lines + 16px` therefore gives a three-line content box with 8px of padding
still painted below it — visible space, inside the clip, showing the first 8px
of the next line. The same padding also made `scrollHeight > clientHeight`
true for a command that hid nothing, so a three-line command would have worn a
fold bar promising to reveal nothing.

**Fix.** The vertical padding moved to the frame (`.user-input-command`) and
the `pre` kept only horizontal padding, so the clip lands exactly on the last
visible line and the overflow test compares like with like. Measured after:
three lines → no bar; four lines → `+1 行`; seventeen → `+14 行`, and the
opened block scrolls to its last line.

**Invariant.** A clamp counted in lines puts the vertical padding on the
frame, never on the clipped element. And `scrollHeight > clientHeight` only
answers "is something hidden" when no padding sits between the two — read the
line boxes with `Range.getClientRects()` when the answer matters.

**Harness note.** Measure geometry and take the screenshot in the *same*
session at the *same* deviceScaleFactor. Reading a screenshot from one run
against numbers from another cost two rounds here: the layouts were 50px
apart and the arithmetic kept insisting the fourth line could not be visible.

## 派发一个合成按键不等于送达：效果必须被确认，而不是被假设

**Symptom.** 桌面壳的全局热键第一次按下：弹窗窗口出现了，但里面是空白页——
合成 keydown 明明已经 `evaluate_js` 派发，页内 QuickCapture 却没有打开。

**Root cause.** 插件的快捷键监听要等它自己的配置请求返回才挂上
（QuickCapture mount → fetch 配置 → setCombo → 才有 listener）。弹窗窗口是
懒创建的：Python 在 `events.loaded` 后立刻 show 并派发，此刻 React 刚挂载、
配置 fetch 还在路上——派发落在无人监听的窗口上，事件不会排队，静默丢失。
浏览器里的同一段代码从不出错，因为监听早已就位。

**Fix.** 送达以"效果"为准：Python 派发后轮询核心自己拥有的 DOM 信号
（`.popup-holder` 的子节点——插件组件渲染出来的东西），没出现就有界补发
（6 次 × 280ms），超限打日志。竞速不可能在发送侧根治，只能在确认侧闭环。

**Invariant.** 任何"发出去就当生效"的跨边界派发（synthetic event、
evaluate_js、js_api 单向调用）都要找一个核心拥有的可观测信号确认落地，
并带重试上限。对方"还没准备好"不是异常路径，是首次交互的常态。

## 改共享配置清单：追加条目，不要重写清单

**Symptom.** 给 pyproject.toml 的 `[[tool.mypy.overrides]]` 加 webview 时
直接替换了 module 列表，mypy 立刻冒出 5 个与本次改动无关的
`import-untyped`（sqlite_vec/yaml/mammoth/pymupdf4llm），像是改动引入的。

**Root cause.** 那个列表是给无 stub 依赖的白名单，我误以为它是本次任务的
占位。共享配置里的清单几乎总是别人的决定，重写 = 静默撤销。

**Fix.** 恢复原条目、追加新条目；用 `git stash` + 重跑对比确认基线，把
"既有失败"和"我引入的失败"分开记账（本仓 HEAD 本就有 15 个 mypy 既有错）。

**Invariant.** 动任何共享清单（mypy overrides、ruff select、tsconfig
paths、vitest include）前先确认它管着谁；验证基线的手段是 stash 后重跑，
不是凭印象。

## 重定向后的 stdout 是全缓冲的：启动日志要显式 flush

**Symptom.** 壳启动 6 秒后日志文件仍是空的，第一轮排查（窗口是否存在）
全靠 Win32 API 盲猜。

**Root cause.** `Start-Process -RedirectStandardOutput` 下 Python 的 stdout
是块缓冲，`print` 不 flush 就滞留在进程内存里；进程不退出日志就不落地。

**Invariant.** 会被重定向的常驻进程，日志一律 `print(..., flush=True)` 或
logging 到 stderr。排障时若日志缺失，先怀疑缓冲而不是"没执行到"。

## Windows 进程清理的两条纪律（本次实测）

- Git Bash 会把 `taskkill /PID` 的 `/PID` 改写成路径——用 `cmd //c
  "taskkill /PID <pid> /T /F"`（双斜杠或套 cmd）。
- `taskkill /T` 对 uv → python 的进程树不可靠（孙进程可能幸存）。杀完用
  `FindWindowW`/`tasklist` 按窗口名和 PID 复核，见窗口还在就定点补杀；
  只杀自己启动的 PID，永不按映像名。

## pythonnet 的命名空间导入要等程序集就绪：借已加载它的模块导

**Symptom.** `from Microsoft.Win32 import SystemEvents` 写在 `webview.start()`
之前执行，报 `No module named 'Microsoft'`——而 pywebview 自己的 winforms.py
第 33 行就是同一条导入，看起来应该没问题。

**Root cause.** pythonnet 的命名空间导入依赖程序集已加载。pywebview 是懒加载
winforms 后端的（`webview.start()` 时才 import），在那之前 clr/System.dll 都
没就绪；同样的导入在 winforms.py 模块体内成功，在我的模块顶层失败。

**Fix.** `from webview.platforms.winforms import SystemEvents`——借 pywebview
的模块导入顺带完成程序集装载。pywebview 若换程序集布局，它自己的导入会更着
变，我跟着走比自己 AddReference 稳。

**Invariant.** 跨包的扩展机制（clr、C 扩展、懒加载插件）依赖宿主的初始化顺序；
需要宿主的符号时，从宿主已初始化的模块借，而不是重复其初始化条件。

## GUI 线程回调里不要走封送：pywebview 的 hide() 是无条件 Invoke

**Symptom.** closing 事件里想把「关闭」变成「隐藏到托盘」，担心在 GUI 线程
里调 `window.hide()` 死锁或重入。

**Root cause.** pywebview 的 `hide()` 实现是**无条件** `self.Invoke(...)`
（show/maximize 等都带 `InvokeRequired` 分支，hide 没带）。GUI 线程内再进
消息泵封送，属于可避免的重入。

**Fix.** closing handler 同步跑在 GUI 线程，直接调原生
`window.native.Hide()`——这正是 WinForms「e.Cancel + Hide 收托盘」的官方写法，
零封送。先读后端库源码确认哪些方法带 InvokeRequired 分支，别按 API 一致性猜。

**Invariant.** 事件回调的宿主线程要先确认（pywebview 的 closing 是
should_lock 同步 GUI 线程），回调体内只做同线程操作或直接原生调用；
跨线程封送只留给真正的异线程调用方（托盘线程、热键线程）。

## pythonw 下 print 是静默空操作：自启动进程要行为验证而不是日志验证

**Symptom.** 模拟注册表自启动（`pythonw autostart_launch.pyw --hidden`）后
拿不到任何日志，无法确认壳真的起来了。

**Root cause.** pythonw 的 `sys.stdout` 是 `None`，CPython 3 的 `print` 对
None 目标静默跳过（不报错也不落盘）——代码不会崩，但所有日志蒸发。

**Fix/Probe.** 行为验证代替日志验证：进程存活（`Get-Process -Id`）、无可见
主窗口（`MainWindowTitle == ''` + `IsWindowVisible`）。排障手段：临时改用
`python.exe` 跑同一入口收集日志。

**Invariant.** 无控制台的宿主（pythonw/服务/托盘进程）不能依赖 stdout 排障；
要么落文件日志，要么按进程行为断言。写"静默运行"的入口时就要想好它坏了怎么查。

## 启动脚本只在 dist 缺失时构建 = 静默提供过期应用

**Symptom.** 按热键唤出便签，弹出的是 520×500 的"缩小版整个对话窗口"；
右上角 ✕ 关不掉它（点的是主窗口的 WindowControls——整页聊天被渲染进了
弹窗）；从任务栏关掉弹窗后，热键从此唤不出任何东西。

**Root cause.** 三个症状一条链。`start-desktop.ps1` 只在 `frontend/dist`
**不存在**时构建，弹窗路由写完之后壳一直在服务没有该路由的旧 bundle：
`/popup/notepad` 落进 SPA catch-all 被重定向到 `/`。错误内容引出后两个：
✕ 是主窗口的控制钮（`hide_main` 隐藏的是主窗口）；而任务栏关闭暴露了
Python 侧从未有过的清理——弹窗的 `closed` 事件没人订阅，`Shell.popups`
留着已销毁的窗口，热键从此在对僵尸窗口操作。

**Invariant.** 启动脚本用「最新源码 mtime vs `dist/index.html`」判断过期
（覆盖 `frontend/src`、前端配置、`plugin/*/frontend`——插件前端被
import.meta.glob 折进主 bundle），过期即重建。弹窗创建时必须订阅
`closed`：`Shell.popups`/`Shell.visible` 里只允许存在活着的窗口，壳外
关闭（任务栏、Alt+F4）与主动销毁共用同一个清理。

## 无 BOM 的 UTF-8 脚本会被 PowerShell 5.1 当 GBK 读

**Symptom.** `start-desktop.ps1`（UTF-8、中文注释）解析报 3 个错误——
「字符串缺少终止符」，报错行与出错行相距很远（43/47/58 行互相连坐）。

**Root cause.** Windows PowerShell 5.1 对无 BOM 文件按 ANSI 代码页（本机
GBK）解码，多字节 UTF-8 序列被重新配对后字节错位，可能吞掉引号；语法
错误在离肇因很远的地方冒出来。PowerShell 7 默认 UTF-8，同一份文件在
pwsh 下正常、在 `powershell` 下报错——"在我机器上能跑"的经典来源。

**Invariant.** 含非 ASCII 字符的 `.ps1` 一律存成 UTF-8 **带 BOM**。改完
脚本用 `[System.Management.Automation.Language.Parser]::ParseFile` 做一次
解析验证，别等运行时暴露。

## pywebview 的 loaded 事件跑在 worker 线程：处理器里碰会 Invoke 回 GUI 的属性 = 全壳死锁

**Symptom.** 修弹窗任务栏图标的第一版（loaded 事件里设
`window.native.ShowInTaskbar = False`）让整个壳死锁：按一次热键后主窗口
"未响应"，弹窗永不出示，热键线程永远卡在 `loaded.wait()`，控制台连一行
都不打印。py-spy 抓到三向互等：MainThread 卡在 `create_window` 的
`i.Invoke` 同步等待（winforms.py:834），`generate_js_object` 线程在等事件
处理器返回，处理器线程在等 `ShowInTaskbar` 的 Invoke 回 GUI——循环等待。

**Root cause.** pywebview 的 `loaded` 事件把处理器派发到 **worker 线程**
（event.py 的 set 为每个处理器起线程），而 `shown`/`closing` 在 GUI 线程
上同步跑。WinForms 控制属性从非拥有线程设置会 Invoke 回 GUI 线程，
`ShowInTaskbar` 的 setter 还会 RecreateHandle。GUI 此时正处于
create_window 的同步等待中，谁也动不了。

**Invariant.** 摸 pywebview 窗口的原生侧只有一条安全路：**GUI 线程上的
事件（shown）+ 原地 SetWindowLongW 位操作**（`_restore_sizing_styles` /
`_strip_taskbar_icon` 的模式）——不重建句柄、不跨线程封送。拿不准某个
事件在哪个线程触发时，先 `uvx py-spy dump --pid <pid>` 看各线程的栈再
动手；凡是会"等 GUI"的调用（同步属性 setter、Invoke）都不能出现在事件
处理器里，除非确认处理器已在 GUI 线程上。查"窗口未响应"用
`IsHungAppWindow`，一个 ctypes 调用就能定位是哪条线程停了泵。

## 弹窗窗口里不允许存在任何页内导航出口

**Symptom.** 呼出弹窗里点「打开」页签的一条便签后，弹窗变成"缩小版的
整个对话窗口"，闪烁、✕ 关不掉、热键从此唤不出正常卡片，控制台反复
`插件 notepad 的弹窗内容迟迟没有渲染`。

**Root cause.** QuickCapture 的 `openNote` 用 `navigate('/notes?…')` 跳转
——在弹窗窗口自己的 SPA 里离开了 `/popup/notepad`，App.tsx 随路由渲染
整个主应用进 520px 小窗。`.popup-holder` 随之消失：`_deliver_summon` 的
6 次合成热键每次都命中主应用挂的页内 quickCapture（一开一关 = 闪烁）
然后宣告失败；窗口控制 ✕ 调 `hide_main()` 隐藏的是主窗口；弹窗被摘出
任务栏后又无从壳外关闭——坏窗口永久滞留，每次热键重新示出。

**Invariant.** 壳弹窗窗口（`/popup/<id>`）内的组件**永远不做 router
导航**；"跳到别处看" 的需求一律变成组件内的视图切换（本次：
NoteDetail 就地查看/编辑）。Python 侧配 `_deliver_summon` 的地址核对
（跑偏先 `location.href` 拽回）作自愈护栏——前端约束防发生，护栏防复发。
排查这类问题先看 `location.pathname` 是否还在 `/popup/<id>`。

## CDP 探针里 `el.click() ?? false` 恒为 false

**Symptom.** 探针的点击步全部报"没有落点"，但页面行为（卡片切换）明明
发生了。

**Root cause.** DOM 的 `click()` 返回 `undefined`，`?? false` 把它兜成
false——点击生效了，表达式却在说谎。

**Invariant.** CDP 派发点击用 IIFE 包装返回真实成败：
`(() => { const el = …; if (el) { el.click(); return true } return false })()`。
