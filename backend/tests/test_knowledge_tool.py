from __future__ import annotations

from typing import Any

import pytest

from app.knowledge import ImportItem
from app.knowledge_tool import KNOWLEDGE_TOOLS, KnowledgeToolService
from app.runtime import CoreServices

CONTENT = (
    "# SQL 注入测试用例\n"
    "## 用户管理\n"
    "### 登录\n"
    "弱口令登录用例。\n"
    "### 搜索\n"
    "搜索框注入用例。\n"
    "## 订单管理\n"
    "下单接口的注入用例。\n"
)


async def _tool(core: CoreServices) -> KnowledgeToolService:
    await core.knowledge.import_items(
        [ImportItem(filename="sql-cases.md", content=CONTENT.encode())],
        project_id=None,
    )
    return KnowledgeToolService(core.store, core.knowledge)


async def _one(service: KnowledgeToolService, command: str) -> str:
    return await service.execute(command, project_id=None)


@pytest.mark.asyncio
async def test_list_reports_documents_with_shape(core: CoreServices) -> None:
    output = await _one(await _tool(core), "list")
    assert "共 1 份文档" in output
    assert "sql-cases.md" in output
    assert "SECTIONS: 5" in output
    assert "id=" in output


@pytest.mark.asyncio
async def test_inspect_shows_the_full_outline(core: CoreServices) -> None:
    output = await _one(await _tool(core), "inspect sql-cases")
    assert "FILE: sql-cases.md" in output
    assert "### 登录" in output
    assert "### 搜索" in output


@pytest.mark.asyncio
async def test_read_by_section_and_by_filename(core: CoreServices) -> None:
    service = await _tool(core)
    whole = await _one(service, "read sql-cases.md")
    assert "弱口令登录用例。" in whole
    section = await _one(service, "read sql-cases --section 1.1.1")
    assert "弱口令登录用例。" in section
    assert "下单接口" not in section


@pytest.mark.asyncio
async def test_read_multiple_sections_in_one_call(core: CoreServices) -> None:
    output = await _one(await _tool(core), "read sql-cases --sections 1.1.1,1.2")
    assert "弱口令登录用例。" in output
    assert "下单接口的注入用例。" in output
    assert "搜索框注入用例。" not in output


@pytest.mark.asyncio
async def test_read_unknown_section_lists_what_exists(core: CoreServices) -> None:
    output = await _one(await _tool(core), "read sql-cases --section 9.9")
    assert "no such section: 9.9" in output
    assert "1.1.1" in output


@pytest.mark.asyncio
async def test_grep_hits_sections_and_says_what_it_is(core: CoreServices) -> None:
    output = await _one(await _tool(core), "grep 注入")
    assert "混合检索" in output
    assert "《sql-cases》" in output
    assert "1.1.2" in output
    assert "--section" in output


@pytest.mark.asyncio
async def test_document_selector_errors_name_the_problem(core: CoreServices) -> None:
    service = await _tool(core)
    assert "no such document" in await _one(service, "inspect 不存在的文档")
    assert "只接受一个文档" in await _one(service, "read sql sql-cases")


@pytest.mark.asyncio
async def test_unknown_command_and_empty_command(core: CoreServices) -> None:
    service = await _tool(core)
    assert "未知命令" in await _one(service, "rm -rf /")
    assert "空命令" in await _one(service, "   ")


@pytest.mark.asyncio
async def test_grep_without_query_is_an_error(core: CoreServices) -> None:
    assert "缺少检索词" in await _one(await _tool(core), "grep")


def test_tool_definition_is_single_function_with_command() -> None:
    assert len(KNOWLEDGE_TOOLS) == 1
    function = KNOWLEDGE_TOOLS[0]["function"]
    assert function["name"] == "knowledge"
    assert function["parameters"]["required"] == ["command"]
    properties: dict[str, Any] = function["parameters"]["properties"]
    assert set(properties) == {"command"}
