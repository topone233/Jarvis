from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.errors import NotFoundError, ValidationError
from app.runtime import CoreServices
from app.skills import (
    SKILL_FILE,
    SKILLS_DISABLED,
    SkillService,
    SkillToolService,
    parse_skill_md,
    validate_skill,
)

SKILL_MD = """---
name: demo
description: 演示技能，把句子倒过来说。
---

把用户的句子倒序输出，不要解释。
"""

BROKEN_MD = "没有 frontmatter 的普通 markdown。"


def _write_skill(source: Path, text: str = SKILL_MD) -> Path:
    source.mkdir(parents=True)
    (source / SKILL_FILE).write_text(text, encoding="utf-8")
    return source


@pytest.fixture
def skills(core: CoreServices) -> SkillService:
    """The real service over the core fixture's data directory."""
    return SkillService(core.store, core.database.data_directory)


# --- frontmatter ---------------------------------------------------------


def test_frontmatter_splits_name_and_body() -> None:
    frontmatter, body = parse_skill_md(SKILL_MD)
    assert frontmatter["name"] == "demo"
    assert "倒序输出" in body
    assert "---" not in body


def test_frontmatter_accepts_multiline_description() -> None:
    frontmatter, _ = parse_skill_md(
        "---\nname: demo\ndescription: >-\n  第一行\n  第二行\n---\n正文"
    )
    assert frontmatter["description"] == "第一行 第二行"


@pytest.mark.parametrize(
    "text",
    [
        "没有 frontmatter 的普通 markdown。",
        "---\nname: demo\n",
        "---\n只是一段文字\n---\n",
    ],
    ids=["no-frontmatter", "unclosed", "not-a-mapping"],
)
def test_frontmatter_rejects_malformed_files(text: str) -> None:
    with pytest.raises(ValidationError):
        parse_skill_md(text)


def test_validate_rejects_bad_names() -> None:
    with pytest.raises(ValidationError, match="不合法"):
        validate_skill({"name": "Demo", "description": "d"}, where=SKILL_FILE)
    with pytest.raises(ValidationError, match="不合法"):
        validate_skill({"name": "-demo-", "description": "d"}, where=SKILL_FILE)


def test_validate_rejects_missing_name_and_description() -> None:
    with pytest.raises(ValidationError, match="name"):
        validate_skill({"description": "d"}, where=SKILL_FILE)
    with pytest.raises(ValidationError, match="description"):
        validate_skill({"name": "demo"}, where=SKILL_FILE)


def test_validate_rejects_reserved_name() -> None:
    with pytest.raises(ValidationError, match="保留名"):
        validate_skill({"name": "compact", "description": "d"}, where=SKILL_FILE)


# --- import --------------------------------------------------------------


def test_import_copies_the_whole_folder(skills: SkillService, tmp_path: Path) -> None:
    source = _write_skill(tmp_path / "anywhere" / "demo")
    (source / "scripts").mkdir()
    (source / "scripts" / "echo.py").write_text("print('ok')", encoding="utf-8")

    info = skills.import_from_path(str(tmp_path / "anywhere" / "demo"))

    assert info["name"] == "demo"
    assert info["enabled"] is True  # 默认开启
    assert (skills.skills_dir / "demo" / "scripts" / "echo.py").is_file()


def test_import_uses_the_frontmatter_name_not_the_folder_name(
    skills: SkillService, tmp_path: Path
) -> None:
    _write_skill(tmp_path / "whatever")
    skills.import_from_path(str(tmp_path / "whatever"))
    assert (skills.skills_dir / "demo").is_dir()
    assert not (skills.skills_dir / "whatever").exists()


def test_import_conflict_needs_a_delete_first(skills: SkillService, tmp_path: Path) -> None:
    _write_skill(tmp_path / "one")
    _write_skill(tmp_path / "two")
    skills.import_from_path(str(tmp_path / "one"))
    with pytest.raises(ValidationError, match="同名"):
        skills.import_from_path(str(tmp_path / "two"))


def test_import_rejects_missing_dir_and_missing_skill_file(
    skills: SkillService, tmp_path: Path
) -> None:
    with pytest.raises(ValidationError, match="目录不存在"):
        skills.import_from_path(str(tmp_path / "nowhere"))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValidationError, match="SKILL.md"):
        skills.import_from_path(str(empty))


def test_import_rejects_bad_frontmatter(skills: SkillService, tmp_path: Path) -> None:
    source = _write_skill(tmp_path / "bad", BROKEN_MD)
    with pytest.raises(ValidationError):
        skills.import_from_path(str(source))
    assert not (skills.skills_dir / "demo").exists()


# --- listing / enable / delete -------------------------------------------


def test_list_reports_broken_folders_without_hiding_them(
    skills: SkillService, tmp_path: Path
) -> None:
    _write_skill(tmp_path / "good")
    skills.import_from_path(str(tmp_path / "good"))
    broken = skills.skills_dir / "hand-copied"
    broken.mkdir()
    (broken / SKILL_FILE).write_text(BROKEN_MD, encoding="utf-8")

    listed = {skill["name"]: skill for skill in skills.list_skills()}

    assert listed["demo"]["broken"] is False
    assert listed["hand-copied"]["broken"] is True
    assert listed["hand-copied"]["enabled"] is False
    assert "frontmatter" in listed["hand-copied"]["error"]


def test_toggle_round_trip_and_back_to_the_default(skills: SkillService, tmp_path: Path) -> None:
    _write_skill(tmp_path / "demo")
    skills.import_from_path(str(tmp_path / "demo"))

    assert skills.set_enabled("demo", False)["enabled"] is False
    assert skills.store.get_setting(SKILLS_DISABLED) == ["demo"]
    assert skills.set_enabled("demo", True)["enabled"] is True
    # 全部回到启用 = 代码默认 = 设置行整个消失。
    assert skills.store.get_setting(SKILLS_DISABLED) is None


def test_delete_removes_folder_and_disable_entry(skills: SkillService, tmp_path: Path) -> None:
    source = _write_skill(tmp_path / "demo")
    skills.import_from_path(str(source))
    skills.set_enabled("demo", False)

    skills.delete_skill("demo")

    assert not (skills.skills_dir / "demo").exists()
    assert skills.store.get_setting(SKILLS_DISABLED) is None
    with pytest.raises(NotFoundError):
        skills.get("demo")


def test_delete_accepts_an_outside_the_charset_folder_name(
    skills: SkillService, tmp_path: Path
) -> None:
    """A hand-copied folder with an off-spec name still deserves a delete."""
    directory = skills.skills_dir / "My_Thing"
    directory.mkdir(parents=True)
    (directory / SKILL_FILE).write_text(BROKEN_MD, encoding="utf-8")

    skills.delete_skill("My_Thing")

    assert not directory.exists()


def test_get_rejects_names_that_are_not_path_segments(skills: SkillService) -> None:
    with pytest.raises(ValidationError):
        skills.get("../outside")


# --- slash matching --------------------------------------------------------


def test_match_slash_hits_enabled_skills_only(skills: SkillService, tmp_path: Path) -> None:
    _write_skill(tmp_path / "demo")
    skills.import_from_path(str(tmp_path / "demo"))

    assert skills.match_slash("/demo 帮我倒序") is not None
    assert skills.match_slash("/demo，帮个忙") is not None  # 中文标点也是边界
    assert skills.match_slash("/demo") is not None

    skills.set_enabled("demo", False)
    assert skills.match_slash("/demo 帮我倒序") is None
    assert skills.match_slash("/unknown 命令") is None
    assert skills.match_slash("普通消息 /demo") is None
    assert skills.match_slash("/demo-2 不该匹配") is None


# --- the `skill` tool ------------------------------------------------------


@pytest.fixture
def installed(skills: SkillService, tmp_path: Path) -> SkillService:
    source = _write_skill(tmp_path / "demo")
    (source / "scripts").mkdir()
    (source / "scripts" / "echo.py").write_text(
        "import sys\nprint('回声', ' '.join(sys.argv[1:]))", encoding="utf-8"
    )
    skills.import_from_path(str(source))
    return skills


async def _one(service: SkillToolService, command: str) -> str:
    return await service.execute(command)


async def test_load_returns_body_and_file_listing(installed: SkillService) -> None:
    output = await _one(SkillToolService(installed), "load demo")
    assert "倒序输出" in output
    assert "scripts/echo.py" in output


async def test_load_refuses_disabled_and_unknown(installed: SkillService) -> None:
    installed.set_enabled("demo", False)
    service = SkillToolService(installed)
    assert "已停用" in await _one(service, "load demo")
    assert "没有这个技能" in await _one(service, "load missing")


async def test_run_executes_a_script_with_args_in_its_own_directory(
    installed: SkillService, tmp_path: Path
) -> None:
    output = await _one(SkillToolService(installed), "run demo scripts/echo.py 甲 乙")
    assert "回声 甲 乙" in output


async def test_run_refuses_outside_scripts_and_unknown_extensions(
    installed: SkillService, tmp_path: Path
) -> None:
    service = SkillToolService(installed)
    outside = tmp_path / "outside.py"
    outside.write_text("print('no')", encoding="utf-8")
    # 正斜杠写法让 shlex 原样保留路径；绝对路径拼进技能目录后指到目录外。
    assert "不在技能" in await _one(service, f"run demo {outside.as_posix()}")
    assert "no such script" in await _one(service, "run demo scripts/none.py")
    assert "回声" in await _one(service, "run demo scripts/echo.py")
    (installed.skills_dir / "demo" / "notes.txt").write_text("x", encoding="utf-8")
    output = await _one(service, "run demo notes.txt")
    assert "支持的类型" in output and ".py" in output


async def test_run_refuses_sh_scripts_with_the_supported_list(
    installed: SkillService,
) -> None:
    script = installed.skills_dir / "demo" / "go.sh"
    script.write_text("echo hi", encoding="utf-8")
    output = await _one(SkillToolService(installed), "run demo go.sh")
    assert "支持的类型" in output


async def test_run_captures_a_failure_and_its_exit_code(installed: SkillService) -> None:
    script = installed.skills_dir / "demo" / "fail.py"
    script.write_text("import sys\nprint('坏输出')\nsys.exit(3)", encoding="utf-8")
    output = await _one(SkillToolService(installed), "run demo fail.py")
    assert "坏输出" in output
    assert "退出码 3" in output


async def test_run_kills_a_script_that_overruns_the_timeout(
    installed: SkillService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.skills.SCRIPT_TIMEOUT_SECONDS", 0.2)
    script = installed.skills_dir / "demo" / "slow.py"
    script.write_text("import time\ntime.sleep(5)", encoding="utf-8")
    output = await _one(SkillToolService(installed), "run demo slow.py")
    assert "超过 0.2 秒" in output


async def test_run_truncates_enormous_output(
    installed: SkillService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.skills.SCRIPT_OUTPUT_MAX_CHARS", 100)
    script = installed.skills_dir / "demo" / "loud.py"
    script.write_text("print('x' * 500)", encoding="utf-8")
    output = await _one(SkillToolService(installed), "run demo loud.py")
    assert "已截断" in output
    assert len(output) < 200


async def test_unknown_and_empty_commands_name_the_usage(skills: SkillService) -> None:
    service = SkillToolService(skills)
    assert "未知命令" in await _one(service, "rm -rf /")
    assert "空命令" in await _one(service, "   ")
    assert "需要恰好一个技能名" in await _one(service, "load")
    assert "run 需要" in await _one(service, "run demo")


def test_tool_definition_is_single_function_with_command() -> None:
    from app.skills import SKILL_TOOLS

    assert len(SKILL_TOOLS) == 1
    function: dict[str, Any] = SKILL_TOOLS[0]["function"]
    assert function["name"] == "skill"
    assert function["parameters"]["required"] == ["command"]


# --- the HTTP surface ---------------------------------------------------------


def test_the_api_round_trips_import_list_toggle_and_delete(
    client: TestClient, tmp_path: Path
) -> None:
    source = tmp_path / "demo-src"
    source.mkdir()
    (source / SKILL_FILE).write_text(SKILL_MD, encoding="utf-8")

    created = client.post("/api/skills/import", json={"path": str(source)})
    assert created.status_code == 201
    assert created.json()["name"] == "demo"

    listed = client.get("/api/skills").json()
    assert listed == [
        {
            "name": "demo",
            "description": "演示技能，把句子倒过来说。",
            "enabled": True,
            "broken": False,
            "error": None,
        }
    ]

    flipped = client.put("/api/skills/demo/enabled", json={"enabled": False})
    assert flipped.status_code == 200
    assert flipped.json()["enabled"] is False

    removed = client.delete("/api/skills/demo")
    assert removed.status_code == 204
    assert client.get("/api/skills").json() == []


def test_the_api_reports_unknown_and_unusable(client: TestClient, tmp_path: Path) -> None:
    assert client.delete("/api/skills/missing").status_code == 404
    assert client.put("/api/skills/missing/enabled", json={"enabled": True}).status_code == 404
    assert client.post("/api/skills/import", json={"path": ""}).status_code == 422
    missing = client.post("/api/skills/import", json={"path": str(tmp_path / "nowhere")})
    assert missing.status_code == 422
    assert "目录不存在" in missing.json()["detail"]
    empty = tmp_path / "empty"
    empty.mkdir()
    no_skill = client.post("/api/skills/import", json={"path": str(empty)})
    assert no_skill.status_code == 422
    assert "SKILL.md" in no_skill.json()["detail"]


def test_a_broken_folder_comes_back_as_a_card(client: TestClient, core: CoreServices) -> None:
    """A hand-copied folder that cannot be used is visible, and deletable."""
    directory = core.skills.skills_dir / "My_Thing"
    directory.mkdir(parents=True)
    (directory / SKILL_FILE).write_text(BROKEN_MD, encoding="utf-8")

    listed = client.get("/api/skills").json()

    assert listed[0]["name"] == "My_Thing"
    assert listed[0]["broken"] is True
    assert listed[0]["enabled"] is False
    assert "frontmatter" in listed[0]["error"]
    # And the off-spec name is still deletable through the API.
    assert client.delete("/api/skills/My_Thing").status_code == 204
