"""Сквозная проверка: сервер поднимается и говорит по MCP.

Сеть не нужна — рукопожатие и список инструментов отдаются без запросов к сайту.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "search_products",
    "product_details",
    "suggest",
    "list_filters",
    "list_regions",
    "set_region",
    "current_store",
    "find_stores",
    "store_details",
    "cart_view",
    "cart_add",
    "auth_status",
    "auth_send_code",
    "auth_login",
    "auth_logout",
}


async def _list_tools(env: dict[str, str]):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "winelab_mcp"], env=env
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        tools = await session.list_tools()
        return init, tools.tools


@pytest.fixture
def child_env(tmp_path):
    env = dict(os.environ)
    env["WINELAB_CONFIG_DIR"] = str(tmp_path)  # не трогаем профиль пользователя
    env.pop("WINELAB_COOKIES", None)
    env["PYTHONPATH"] = os.getcwd()
    return env


def test_stdio_handshake_and_tools(child_env):
    init, tools = asyncio.run(asyncio.wait_for(_list_tools(child_env), timeout=60))
    assert init.serverInfo.name == "winelab"

    names = {t.name for t in tools}
    assert names >= EXPECTED_TOOLS, f"пропали инструменты: {EXPECTED_TOOLS - names}"

    # каждый инструмент должен объяснять себя модели
    undocumented = [t.name for t in tools if not (t.description or "").strip()]
    assert not undocumented, f"без описания: {undocumented}"


def test_search_tool_exposes_filters(child_env):
    _, tools = asyncio.run(asyncio.wait_for(_list_tools(child_env), timeout=60))
    search = next(t for t in tools if t.name == "search_products")
    props = set(search.inputSchema["properties"])
    assert {"query", "in_stock_only", "min_price", "max_price", "sort"} <= props


def test_server_does_not_touch_network_on_startup(child_env):
    """Импорт сервера не должен ходить в сеть: хосты запускают его до диалога."""
    child_env["WINELAB_BASE"] = "http://127.0.0.1:9"  # закрытый порт
    init, tools = asyncio.run(asyncio.wait_for(_list_tools(child_env), timeout=60))
    assert init.serverInfo.name == "winelab" and tools
