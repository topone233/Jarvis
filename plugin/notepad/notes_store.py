"""The notepad plugin's storage: markdown files are the truth.

One note is one ``.md`` file in ``<数据目录>/notes/`` - YAML frontmatter
carrying the metadata (title, tags, timestamps, which surface created it),
the body carrying the markdown a human wrote. Nothing else holds a copy: no
table, no derived index on disk. The costs of that honesty are paid here, in
one place:

- An in-memory index maps id -> parsed note. ``refresh()`` re-stats the
  directory on every request and re-parses only files whose mtime or size
  moved, so an edit made in Typora - or any other editor - shows up on the
  next call without a watcher, and without re-reading every file.
- An externally touched file has its ``updated_at`` raised to the file's
  mtime in memory (past a one-second tolerance, which is what keeps our own
  writes from looking like edits from outside). That raised timestamp is
  also the conflict token: a client saving against an older ``updated_at``
  is refused, which is how a Typora edit open in another window loses
  nothing to a silent overwrite.
- Deletion moves the file into ``notes/.deleted/``; restore moves it back,
  purge unlinks. The dot keeps the working directory - the thing a human
  browses - clean.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.errors import NotFoundError, ValidationError
from app.utils import new_id, safe_filename, utc_now

DELETED_DIR = ".deleted"

_TITLE_LINE_LIMIT = 160
_EXCERPT_LIMIT = 120
_CONTENT_FOR_INDEX_LIMIT = 20_000
_TAG_MAX = 4
_TAG_LENGTH_MAX = 24

_CJK_AND_WORD = re.compile(r"[\w]+", re.UNICODE)


@dataclass
class Note:
    id: str
    title: str
    tags: list[str]
    source: str
    tag_status: str
    created_at: str
    updated_at: str
    content: str
    file_path: Path

    def summary(self) -> dict[str, Any]:
        """The list-item shape: an excerpt instead of the whole body."""
        return {
            "id": self.id,
            "title": self.title,
            "excerpt": excerpt_of(self.content),
            "tags": list(self.tags),
            "source": self.source,
            "tag_status": self.tag_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def full(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "tags": list(self.tags),
            "source": self.source,
            "tag_status": self.tag_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def excerpt_of(content: str) -> str:
    """The first stretch of real text, flattened - list rows skim, they do
    not render."""
    for line in content.splitlines():
        single = line.strip()
        # Markdown structure is not text: a row that starts at a fence or a
        # heading marker shows the words, not the punctuation.
        single = re.sub(r"^[>#*\-+\s]+", "", single)
        single = single.replace("`", "")
        if single:
            return single[:_EXCERPT_LIMIT] + ("…" if len(single) > _EXCERPT_LIMIT else "")
    return ""


def _slug_of(title: str) -> str:
    return safe_filename(title)[:40] or "note"


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a note file into its frontmatter mapping and body.

    The closing line is the first bare ``---`` or ``...`` after the opening
    one; everything after it is body, ``---`` lines included, because a
    horizontal rule in the body is none of the frontmatter's business.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValidationError("缺少 frontmatter（第一行应是 --- ）。")
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            closing = index
            break
    if closing is None:
        raise ValidationError("frontmatter 没有闭合（缺第二个 --- ）。")
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as error:
        raise ValidationError(f"frontmatter 不是有效的 YAML：{error}") from error
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    return frontmatter, "\n".join(lines[closing + 1 :]).lstrip("\n")


def dump_note(note: Note) -> str:
    """The whole file: frontmatter, then body, both UTF-8 and plain."""
    frontmatter = {
        "id": note.id,
        "title": note.title,
        "tags": note.tags,
        "source": note.source,
        "tag_status": note.tag_status,
        "created_at": note.created_at,
        "updated_at": note.updated_at,
    }
    dumped = yaml.safe_dump(
        frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10_000
    )
    return f"---\n{dumped}---\n\n{note.content}"


def _iso_of(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, tz=UTC).isoformat(timespec="seconds")


@dataclass
class _Entry:
    note: Note
    stat: tuple[int, int]  # (mtime_ns, size) the entry was parsed at


@dataclass
class _DeletedEntry:
    note: Note
    stat: tuple[int, int]
    file_path: Path


class NoteIndex:
    """The notes of one data directory, and everything done to them.

    Every read goes through ``refresh()`` first, so a file changed outside
    the app is simply there on the next call. Writes are atomic (temp file
    plus replace), which is what lets Typora hold the same file without the
    two of them trading torn halves.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._entries: dict[str, _Entry] = {}

    # --- reading ---------------------------------------------------------

    def refresh(self) -> None:
        """Stat the directory and re-parse what moved.

        A file that vanished (deleted outside the app, or moved to
        ``.deleted/``) leaves the index; its id simply stops resolving, the
        same verdict a missing row gets.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        by_path = {entry.note.file_path: entry for entry in self._entries.values()}
        seen: set[str] = set()
        for path in self.root.glob("*.md"):
            stat = path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
            existing = by_path.get(path)
            if existing is not None and existing.stat == key:
                seen.add(existing.note.id)
                continue
            try:
                note = self._parse_file(path)
            except (ValidationError, OSError, UnicodeDecodeError):
                # A hand-broken file (frontmatter mangled in another editor)
                # is skipped rather than allowed to blank the whole list.
                continue
            self._raise_updated_at_if_touched_outside(note, existing, stat)
            seen.add(note.id)
            self._entries[note.id] = _Entry(note=note, stat=key)
        for note_id in set(self._entries) - seen:
            del self._entries[note_id]

    def list_notes(
        self, *, query: str | None, tag: str | None, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        self.refresh()
        notes = [entry.note for entry in self._entries.values()]
        if tag:
            notes = [note for note in notes if tag in note.tags]
        if query:
            scored = _score(notes, query)
            notes = [note for score, note in scored]
        notes.sort(key=lambda note: note.updated_at, reverse=True)
        return [note.summary() for note in notes[offset : offset + limit]]

    def tags(self) -> list[dict[str, Any]]:
        self.refresh()
        counts: dict[str, int] = {}
        for entry in self._entries.values():
            for tag in entry.note.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return [
            {"tag": tag, "count": count}
            for tag, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    def get(self, note_id: str) -> Note:
        self.refresh()
        return self._require(note_id)

    def list_deleted(self) -> list[dict[str, Any]]:
        deleted = self.root / DELETED_DIR
        if not deleted.is_dir():
            return []
        entries: list[dict[str, Any]] = []
        for path in sorted(deleted.glob("*.md")):
            try:
                note = self._parse_file(path)
            except (ValidationError, OSError, UnicodeDecodeError):
                continue
            entries.append(note.summary())
        entries.sort(key=lambda item: item["updated_at"], reverse=True)
        return entries

    # --- writing ---------------------------------------------------------

    def create(self, *, title: str, content: str, source: str) -> Note:
        self.refresh()
        stamp = utc_now()
        note_id = new_id()
        path = self._available_path(_slug_of(title), note_id)
        note = Note(
            id=note_id,
            title=title,
            tags=[],
            source=source,
            tag_status="none",
            created_at=stamp,
            updated_at=stamp,
            content=content,
            file_path=path,
        )
        self._write(note)
        return note

    def update(
        self,
        note_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        tag_status: str | None = None,
        base_updated_at: str | None = None,
    ) -> Note:
        self.refresh()
        note = self._require(note_id)
        if base_updated_at is not None and note.updated_at != base_updated_at:
            # Somebody else - very likely another editor window - changed the
            # file since this client last saw it. Refusing is the whole
            # protection; the client reloads and reapplies.
            raise ValidationError(
                "这条便签刚被外部修改过，保存被拒绝以免覆盖。请刷新后重试。"
            )
        if title is not None:
            note.title = title[:_TITLE_LINE_LIMIT]
        if content is not None:
            note.content = content
        if tags is not None:
            note.tags = _clean_tags(tags)
            # Hand-set tags are a verdict: the AI's pending run must not land
            # on top of them, and there is nothing left to wait for.
            note.tag_status = "done"
        if tag_status is not None:
            note.tag_status = tag_status
        note.updated_at = utc_now()
        self._write(note)
        return note

    def move_to_deleted(self, note_id: str) -> None:
        self.refresh()
        note = self._require(note_id)
        target = self.root / DELETED_DIR
        target.mkdir(exist_ok=True)
        os.replace(note.file_path, target / note.file_path.name)
        del self._entries[note_id]

    def restore(self, note_id: str) -> dict[str, Any]:
        deleted = self.root / DELETED_DIR
        if not deleted.is_dir():
            raise NotFoundError("回收站是空的。")
        for path in deleted.glob("*.md"):
            try:
                note = self._parse_file(path)
            except (ValidationError, OSError, UnicodeDecodeError):
                continue
            if note.id == note_id:
                os.replace(path, self.root / path.name)
                return note.summary()
        raise NotFoundError("回收站里没有这条便签。")

    def purge(self, note_id: str) -> None:
        deleted = self.root / DELETED_DIR
        if not deleted.is_dir():
            raise NotFoundError("回收站里没有这条便签。")
        for path in deleted.glob("*.md"):
            try:
                note = self._parse_file(path)
            except (ValidationError, OSError, UnicodeDecodeError):
                continue
            if note.id == note_id:
                path.unlink()
                return
        raise NotFoundError("回收站里没有这条便签。")

    # --- tagging ---------------------------------------------------------

    def apply_tags(self, note_id: str, tags: list[str]) -> bool:
        """The background tagger's write. Only lands while the note is still
        waiting for it - a hand-set tag list, a deletion, or an external edit
        that arrived first all win over the model's suggestion."""
        self.refresh()
        if note_id not in self._entries:
            return False
        note = self._entries[note_id].note
        if note.tag_status != "pending":
            return False
        note.tags = _clean_tags(tags)
        note.tag_status = "done"
        # The suggestion is metadata, not an edit: the note keeps its place
        # in the list rather than jumping for work the user never did.
        self._write(note, bump_updated_at=False)
        return True

    def mark_tag_failed(self, note_id: str) -> None:
        self.refresh()
        if note_id not in self._entries:
            return
        note = self._entries[note_id].note
        # "none" can become "failed" only when a tagging attempt was actually
        # made - a note created with the switch off keeps "none" forever.
        if note.tag_status in ("pending", "none"):
            note.tag_status = "failed"
            self._write(note, bump_updated_at=False)

    # --- internals -------------------------------------------------------

    def _require(self, note_id: str) -> Note:
        entry = self._entries.get(note_id)
        if entry is None:
            raise NotFoundError("没有这条便签，可能已被删除。")
        return entry.note

    def _available_path(self, slug: str, note_id: str) -> Path:
        """A filename that is not taken: slug plus a growing slice of the id.

        Two notes created in the same second with the same title differ by
        their id suffix; the slice grows until the name is free, which in
        practice is always the first try.
        """
        for width in range(8, len(note_id) + 1):
            candidate = self.root / f"{slug}-{note_id[:width]}.md"
            if not candidate.exists():
                return candidate
        raise ValidationError("无法为这条便签生成文件名。")

    def _write(self, note: Note, *, bump_updated_at: bool = True) -> None:
        if bump_updated_at:
            note.updated_at = utc_now()
        self.root.mkdir(parents=True, exist_ok=True)
        temp = note.file_path.with_suffix(".md.tmp")
        temp.write_text(dump_note(note), encoding="utf-8")
        os.replace(temp, note.file_path)
        stat = note.file_path.stat()
        self._entries[note.id] = _Entry(note=note, stat=(stat.st_mtime_ns, stat.st_size))

    def _parse_file(self, path: Path) -> Note:
        text = path.read_text(encoding="utf-8")
        frontmatter, content = parse_frontmatter(text)
        note_id = str(frontmatter.get("id") or "").strip()
        if not note_id:
            # A file with no id in its frontmatter was made outside the app;
            # its filename stands in, so it is still readable and searchable.
            note_id = f"ext-{path.stem}"
        tags_value = frontmatter.get("tags")
        tags = _clean_tags(tags_value if isinstance(tags_value, list) else [])
        created = str(frontmatter.get("created_at") or "") or _iso_of(path.stat().st_mtime)
        updated = str(frontmatter.get("updated_at") or "") or created
        return Note(
            id=note_id,
            title=str(frontmatter.get("title") or "")[:_TITLE_LINE_LIMIT],
            tags=tags,
            source=str(frontmatter.get("source") or "page"),
            tag_status=str(frontmatter.get("tag_status") or "none"),
            created_at=created,
            updated_at=updated,
            content=content,
            file_path=path,
        )

    def _raise_updated_at_if_touched_outside(
        self, note: Note, existing: _Entry | None, stat: os.stat_result
    ) -> None:
        """Raise ``updated_at`` to the mtime when the file was changed by
        something that is not this process.

        The tolerance is what tells the two apart: our own writes stamp the
        frontmatter and the file in the same breath, so an mtime within a
        second of the recorded value is us. Anything later came from outside,
        and its mtime becomes the timestamp - which is also what a client's
        next save compares against, so the external edit cannot be silently
        overwritten.
        """
        try:
            recorded = datetime.fromisoformat(note.updated_at).timestamp()
        except ValueError:
            recorded = 0.0
        if stat.st_mtime - recorded > 1.0:
            note.updated_at = _iso_of(stat.st_mtime)
        elif existing is not None:
            # Keep whatever a previous refresh decided; a re-parse of an
            # unchanged file must not wobble the conflict token.
            note.updated_at = existing.note.updated_at


def _clean_tags(tags: list[Any]) -> list[str]:
    cleaned: list[str] = []
    for value in tags:
        tag = str(value).strip().strip("#")
        if not tag or tag in cleaned:
            continue
        cleaned.append(tag[:_TAG_LENGTH_MAX])
        if len(cleaned) == _TAG_MAX:
            break
    return cleaned


def _terms(query: str) -> list[str]:
    """The query's search terms: words and CJK bigrams, deduplicated.

    The bigrams are what let a two-character Chinese query find itself inside
    longer runs without a segmentation dependency - the same trick the
    knowledge base's FTS index plays, played over the live notes instead.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for word in _CJK_AND_WORD.findall(query.casefold()):
        if len(word) == 1 or word.isascii():
            if word not in seen:
                seen.add(word)
                terms.append(word)
        else:
            for index in range(len(word) - 1):
                bigram = word[index : index + 2]
                if bigram not in seen:
                    seen.add(bigram)
                    terms.append(bigram)
        if len(terms) == 12:
            break
    return terms


def _score(notes: list[Note], query: str) -> list[tuple[float, Note]]:
    """Every note that clears the floor, best first.

    A term is worth 3 in the title, 2 in the tags, 1 in the body; the score
    is how much of that possible weight the note actually carries. Two guards
    keep the junk out - learned the hard way from keyword retrieval's long
    tail: a multi-term query must match at least two of its terms (a single
    coincidental bigram is not evidence), and everything below max(0.15,
    0.2 × best) is dropped, so a strong title hit does not drag a pile of
    one-bigram accidents along with it.
    """
    terms = _terms(query)
    if not terms:
        return []
    scored: list[tuple[float, Note]] = []
    for view in note_views(notes):
        total = 0.0
        matched = 0
        for term in terms:
            weight = 0.0
            if term in view.title_fold:
                weight += 3.0
            if any(term in tag for tag in view.tags_fold):
                weight += 2.0
            if term in view.content_fold:
                weight += 1.0
            if weight > 0:
                matched += 1
            total += weight
        if len(terms) >= 2 and matched < 2:
            continue
        scored.append((total / (len(terms) * 6.0), view.source_note))
    if not scored:
        return []
    best = max(score for score, _ in scored)
    floor = max(0.15, 0.2 * best)
    kept = [(score, note) for score, note in scored if score >= floor]
    # Score first; equal scores go to the note touched most recently. Two
    # passes because the secondary key wants the opposite direction to the
    # primary one - sort by it first and let the stable sort keep it.
    kept.sort(key=lambda item: item[1].updated_at, reverse=True)
    kept.sort(key=lambda item: item[0], reverse=True)
    return kept


@dataclass
class _NoteView:
    """A note folded to lowercase once per search, plus the back-reference.

    Casefolding the same note's body once per query instead of once per term
    is the whole reason this exists: a 10k-note search casefolds 10k bodies,
    not 120k.
    """

    source_note: Note
    title_fold: str
    tags_fold: list[str]
    content_fold: str


def note_views(notes: list[Note]) -> list[_NoteView]:
    trimmed = _CONTENT_FOR_INDEX_LIMIT
    return [
        _NoteView(
            source_note=note,
            title_fold=note.title.casefold(),
            tags_fold=[tag.casefold() for tag in note.tags],
            content_fold=note.content[:trimmed].casefold(),
        )
        for note in notes
    ]
