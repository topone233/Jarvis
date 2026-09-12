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
