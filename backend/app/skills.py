"""Skills: the generic Agent Skills layer.

A skill is a folder with a SKILL.md at its root - YAML frontmatter carrying
`name` and `description`, the body carrying the instructions - plus whatever
scripts and reference files it ships with. That shape is the one Claude Code
and Codex read, so a folder written for them works here unchanged, and
Jarvis-specific state (whether the skill is on) lives elsewhere: in the
settings KV, keyed by name.

Progressive disclosure is what keeps the prompt cheap. The system instruction
only ever sees the names and descriptions; the full body enters the context
when it is asked for, either by the user typing `/name` or by the model
calling the `skill` tool's load command. Scripts run from the skill's own
directory, because skills reference their assets relatively.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from app.errors import NotFoundError, ValidationError
from app.store import Store

SKILL_FILE = "SKILL.md"

#: The settings row holding the disabled names. There is no row while nothing
#: has been turned off - which is what "skills default to on" means concretely,
#: the same "no row is the code default" model every setting here follows.
SKILLS_DISABLED = "skills_disabled"

#: `/compact` already means something in a conversation, so no skill may take
#: the word. The check lives at import time; a hand-copied folder that claims
#: the name still loses to the compaction shortcut, which runs first.
RESERVED_SKILL_NAMES = {"compact"}

NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX_CHARS = 64
DESCRIPTION_MAX_CHARS = 1024

#: `/name` at the start of a message, where the name ends at the skill-name
#: charset. The negative lookahead is what lets `/name，你好` count as a
#: match while `/name-2` does not bleed into `/name`.
SLASH_PATTERN = re.compile(r"^/([a-z0-9]+(?:-[a-z0-9]+)*)(?![a-z0-9-])")

#: How long a script may run before it is killed, and how much of its output
#: comes back. A hung script must not hang the run; an enormous output must
#: not eat the context window in one tool message.
SCRIPT_TIMEOUT_SECONDS = 120
SCRIPT_OUTPUT_MAX_CHARS = 20_000

USAGE = "用法：load <name> | run <name> <script> [参数...]"

SKILL_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "skill",
            "description": (
                "使用已安装的技能。load <name>：载入技能的完整指令（系统提示词里只有简介，"
                "执行技能前必须先 load）；run <name> <script> [参数...]：运行技能目录里自带的"
                "脚本，脚本路径相对技能根目录。这不是 shell：run 一次只运行一个脚本，"
                "没有管道、通配符和重定向。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "一条命令，例如 load pdf-helper 或 "
                            "run pdf-helper scripts/extract.py input.pdf。" + USAGE
                        ),
                    },
                },
                "required": ["command"],
            },
        },
    },
]


def parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md into its frontmatter mapping and instruction body."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValidationError("SKILL.md 缺少 YAML frontmatter（第一行应是 --- ）。")
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            closing = index
            break
    if closing is None:
        raise ValidationError("SKILL.md 的 frontmatter 没有闭合（缺第二个 --- ）。")
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as error:
        raise ValidationError(f"SKILL.md 的 frontmatter 不是有效的 YAML：{error}") from error
    if not isinstance(frontmatter, dict):
        raise ValidationError("SKILL.md 的 frontmatter 应是键值对。")
    return frontmatter, "\n".join(lines[closing + 1 :]).strip()


def validate_skill(frontmatter: dict[str, Any], *, where: str) -> tuple[str, str]:
    """The (name, description) a skill must declare, or the words saying why not."""
    name = str(frontmatter.get("name", "")).strip()
    description = str(frontmatter.get("description", "")).strip()
    if not name:
        raise ValidationError(f"{where} 缺少 name 字段。")
    if len(name) > NAME_MAX_CHARS or not NAME_PATTERN.fullmatch(name):
        raise ValidationError(
            f"{where} 的 name「{name}」不合法：只能用小写字母、数字和中划线，"
            f"不能以中划线开头或结尾，最长 {NAME_MAX_CHARS} 字符。"
        )
    if name in RESERVED_SKILL_NAMES:
        raise ValidationError(f"技能名 {name} 是保留名（/compact 另有用途），请换一个。")
    if not description:
        raise ValidationError(f"{where} 缺少 description 字段。")
    if len(description) > DESCRIPTION_MAX_CHARS:
        raise ValidationError(f"{where} 的 description 超过 {DESCRIPTION_MAX_CHARS} 字符。")
    return name, description


def _info(
    name: str,
    description: str | None,
    enabled: bool,
    *,
    broken: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "enabled": enabled,
        "broken": broken,
        "error": error,
    }


def _script_command(script: Path) -> list[str] | None:
    """How a script file is launched, by extension, or None for the rest."""
    suffix = script.suffix.lower()
    if suffix == ".py":
        # The backend's own interpreter: always present, no PATH gamble.
        return [sys.executable]
    if suffix == ".ps1":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    if suffix in (".bat", ".cmd"):
        return ["cmd", "/c"]
    return None


def _file_listing(directory: Path) -> str:
    """The skill's own files, so a loaded skill can name what it ships."""
    entries = sorted(
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.name != SKILL_FILE
    )
    if not entries:
        return ""
    lines = [f"- {entry}" for entry in entries[:20]]
    if len(entries) > 20:
        lines.append(f"- …（共 {len(entries)} 个文件）")
    return "\n".join(lines)


def _script_env() -> dict[str, str]:
    """The child environment, with its stdio pinned to UTF-8.

    The output is decoded as UTF-8, and a Windows console would otherwise
    write GBK - pinning both sides is what keeps a script's Chinese output
    legible to the model.
    """
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


async def _run_process(argv: list[str], *, cwd: Path) -> str:
    """One script, captured output, bounded time - the run's whole contract.

    stderr rides with stdout: a traceback is exactly the text the model needs.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=_script_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as error:
        return f"skill: 脚本启动失败：{error}"
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), SCRIPT_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.communicate()
        return f"skill: 脚本运行超过 {SCRIPT_TIMEOUT_SECONDS} 秒，已强制结束。"
    output = stdout.decode("utf-8", errors="replace").rstrip() if stdout else ""
    if len(output) > SCRIPT_OUTPUT_MAX_CHARS:
        output = (
            output[:SCRIPT_OUTPUT_MAX_CHARS]
            + f"\n…（输出超过 {SCRIPT_OUTPUT_MAX_CHARS} 字符，已截断。）"
        )
    if process.returncode not in (0, None):
        output += f"\n（退出码 {process.returncode}）"
    return output or "skill: 脚本没有输出。"


class SkillService:
    """The skills living in the data directory, and whether each is on.

    The filesystem is the source of truth for what exists; the settings KV
    only holds the disabled list. A directory that fails to read comes back
    as a broken entry rather than disappearing - a card the user can see is
    the only way a hand-copied folder with a typo ever gets fixed.
    """

    def __init__(self, store: Store, root: Path) -> None:
        self.store = store
        self.skills_dir = root / "skills"

    # --- reading ---------------------------------------------------------

    def list_skills(self) -> list[dict[str, Any]]:
        if not self.skills_dir.is_dir():
            return []
        disabled = self._disabled()
        return [
            self._describe(directory, disabled)
            for directory in sorted(self.skills_dir.iterdir())
            if directory.is_dir()
        ]

    def get(self, name: str) -> dict[str, Any]:
        return self._describe(self._directory(name), self._disabled())

    def has_enabled(self) -> bool:
        return any(not skill["broken"] and skill["enabled"] for skill in self.list_skills())

    def summary_section(self) -> str | None:
        """The system-prompt section listing what is on, or None for silence."""
        enabled = [
            skill for skill in self.list_skills() if not skill["broken"] and skill["enabled"]
        ]
        if not enabled:
            return None
        lines = [
            "## 可用技能",
            "以下技能各自适用于特定类型的任务。要用某个技能时，先调用 skill 工具的 load 命令"
            "获取完整指令，再按指令执行；技能自带的脚本用 run 命令运行。"
            "用户消息以 /技能名 开头时，表示明确要求使用该技能。",
            *("- /" + skill["name"] + "：" + skill["description"] for skill in enabled),
        ]
        return "\n".join(lines)

    def body_section(self, name: str) -> str:
        """The full instructions, as they ride into the system prompt."""
        return f"## 技能指令：/{name}\n\n{self.load_text(name)}"

    def load_text(self, name: str) -> str:
        """What `skill load` answers: the body, plus what the folder ships."""
        directory = self.usable_directory(name)
        _, body = parse_skill_md((directory / SKILL_FILE).read_text(encoding="utf-8"))
        listing = _file_listing(directory)
        if listing:
            body += "\n\n---\n技能目录下的文件：\n" + listing
        return body

    def usable_directory(self, name: str) -> Path:
        """The directory of a skill that is readable and switched on."""
        directory = self._directory(name)
        skill = self._describe(directory, self._disabled())
        if skill["broken"]:
            raise ValidationError(f"技能 {name} 无法使用：{skill['error']}")
        if not skill["enabled"]:
            raise ValidationError(f"技能 {name} 已停用，请先在设置里开启。")
        return directory

    def match_slash(self, content: str) -> dict[str, Any] | None:
        """The skill a `/name` message names, or None when it names nothing.

        An unknown or disabled name is not an error here: the message simply
        goes to the model as the plain text it also is.
        """
        match = SLASH_PATTERN.match(content.strip())
        if match is None:
            return None
        try:
            skill = self.get(match.group(1))
        except NotFoundError:
            return None
        if skill["broken"] or not skill["enabled"]:
            return None
        return skill

    # --- writing ---------------------------------------------------------

    def import_from_path(self, source: str) -> dict[str, Any]:
        """Copy a local skill folder into the data directory, validated.

        The copy is the whole feature: the source folder stays wherever it
        is - a checkout, a download - and Jarvis keeps its own snapshot, so
        moving or deleting the original changes nothing here.
        """
        origin = Path(source).expanduser().resolve()
        if not origin.exists():
            raise ValidationError(f"目录不存在：{origin}。请检查路径是否正确。")
        if not origin.is_dir():
            raise ValidationError(f"{origin} 是一个文件，不是目录。")
        skill_file = origin / SKILL_FILE
        if not skill_file.is_file():
            raise ValidationError(f"{origin} 里没有 {SKILL_FILE}，不是一个 skill 文件夹。")
        try:
            text = skill_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ValidationError(f"读不了 {SKILL_FILE}：{error}") from error
        frontmatter, _ = parse_skill_md(text)
        name, _ = validate_skill(frontmatter, where=SKILL_FILE)
        target = self.skills_dir / name
        if target.exists():
            raise ValidationError(f"已有同名技能「{name}」，请先在列表里删除它再导入。")
        # Made here rather than assumed: a data directory that predates skills
        # has no such folder yet, and creating it is Jarvis's own bookkeeping.
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(origin, target)
        return self.get(name)

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        self._directory(name)
        disabled = [entry for entry in self._disabled() if entry != name]
        if not enabled:
            disabled.append(name)
        if disabled:
            self.store.set_setting(SKILLS_DISABLED, disabled)
        else:
            # All skills on again is the shipped default: the row would only
            # be a stale copy of that, one later imports would have to scrub.
            self.store.delete_setting(SKILLS_DISABLED)
        return self.get(name)

    def delete_skill(self, name: str) -> None:
        directory = self._directory(name)
        shutil.rmtree(directory)
        disabled = [entry for entry in self._disabled() if entry != name]
        if disabled:
            self.store.set_setting(SKILLS_DISABLED, disabled)
        else:
            self.store.delete_setting(SKILLS_DISABLED)

    # --- helpers ---------------------------------------------------------

    def _disabled(self) -> list[str]:
        stored = self.store.get_setting(SKILLS_DISABLED)
        if not isinstance(stored, list):
            return []
        return [entry for entry in stored if isinstance(entry, str)]

    def _directory(self, name: str) -> Path:
        directory = self.skills_dir / self._clean_name(name)
        if not directory.is_dir():
            raise NotFoundError(f"没有这个技能：{name}。")
        return directory

    @staticmethod
    def _clean_name(name: str) -> str:
        """The name as a single path segment, or the words saying why not.

        Lenient on purpose - a hand-copied folder may be named outside the
        skill charset and still deserves a working delete - but never a path:
        anything carrying a separator or walking upward is refused outright.
        """
        value = name.strip()
        if not value or value in (".", "..") or Path(value).name != value:
            raise ValidationError(f"技能名「{name}」不合法。")
        return value

    def _describe(self, directory: Path, disabled: list[str]) -> dict[str, Any]:
        skill_file = directory / SKILL_FILE
        if not skill_file.is_file():
            return _info(
                directory.name, None, False, broken=True, error=f"目录里没有 {SKILL_FILE}。"
            )
        try:
            text = skill_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            return _info(
                directory.name, None, False, broken=True, error=f"读不了 {SKILL_FILE}：{error}"
            )
        try:
            frontmatter, _ = parse_skill_md(text)
            name, description = validate_skill(frontmatter, where=SKILL_FILE)
        except ValidationError as error:
            return _info(directory.name, None, False, broken=True, error=str(error))
        if name != directory.name:
            return _info(
                directory.name,
                None,
                False,
                broken=True,
                error=f"SKILL.md 的 name（{name}）与目录名（{directory.name}）不一致。",
            )
        return _info(name, description, name not in disabled)


class SkillToolService:
    """Parses and answers one `skill` command per call.

    Stateless like the knowledge tool: repetition guarding and the round
    budget belong to the loop in runs.py, so this stays a pure interpreter
    that is trivial to test. Every failure answers as text - the message is
    the model's only schema.
    """

    def __init__(self, skills: SkillService) -> None:
        self.skills = skills

    async def execute(self, command: str) -> str:
        try:
            tokens = shlex.split(command)
        except ValueError:
            return "skill: 命令解析失败（引号不匹配）。" + USAGE
        if not tokens:
            return "skill: 空命令。" + USAGE
        verb, *arguments = tokens
        if verb == "load":
            return self._load(arguments)
        if verb == "run":
            return await self._run(arguments)
        return f"skill: 未知命令 {verb}。" + USAGE

    def _load(self, arguments: list[str]) -> str:
        if len(arguments) != 1:
            return "skill: load 需要恰好一个技能名。" + USAGE
        try:
            return self.skills.load_text(arguments[0])
        except (NotFoundError, ValidationError) as error:
            return f"skill: {error}"

    async def _run(self, arguments: list[str]) -> str:
        if len(arguments) < 2:
            return "skill: run 需要 <name> <script>，后跟可选参数。" + USAGE
        name, script = arguments[0], arguments[1]
        script_args = arguments[2:]
        try:
            directory = self.skills.usable_directory(name)
        except (NotFoundError, ValidationError) as error:
            return f"skill: {error}"
        # Built from the skill directory itself, so only a `..` in the model's
        # argument can leave it - and then only onto the local disk this
        # process already owns. The containment check is what keeps a script
        # from being named outside the folder it belongs to.
        script_path = (directory / script).resolve()
        if not script_path.is_relative_to(directory.resolve()):
            return f"skill: {script} 不在技能 {name} 的目录里。"
        if not script_path.is_file():
            return f"skill: no such script: {script}。"
        argv = _script_command(script_path)
        if argv is None:
            return (
                f"skill: 不支持直接运行 {script_path.suffix or '无扩展名'} 脚本。"
                "支持的类型：.py、.ps1、.bat、.cmd。"
            )
        return await _run_process([*argv, str(script_path), *script_args], cwd=directory)
