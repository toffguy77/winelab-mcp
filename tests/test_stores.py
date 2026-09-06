"""Выбор магазина: set_store и нормализация адресов."""

from __future__ import annotations

import pytest

from winelab_mcp import server as server_mod
from winelab_mcp.shape import _promotions, slim_store

pytestmark = pytest.mark.usefixtures("mocked")


def test_set_store_switches_session(site):
    r = server_mod.set_store("M487")
    assert r["ok"] is True
    assert r["method"] == "cookie currentPOS"
    assert server_mod.current_store()["code"] == "M487"
    # штатные ручки пробуем до куки — и обе отвечают 405, как живой сайт
    tried = [m for m, _ in site.calls if m == "POST"]
    assert ("GET", "/store-finder/pos-name") in site.calls
    assert tried  # POST-стратегии тоже отработали


def test_set_store_reports_failure(site):
    site.pos_cookie_works = False
    r = server_mod.set_store("M487")
    assert r["ok"] is False and r["error_kind"] == "unsupported"
    assert len(r["attempts"]) == 5
    assert r["current_store"] == "M735"


def test_set_store_rejects_unknown_code(mocked, monkeypatch):
    from winelab_mcp.client import BlockedError

    def boom(code):
        raise BlockedError("HTML вместо JSON")

    monkeypatch.setattr(mocked, "store", boom)
    r = server_mod.set_store("M000")
    assert r["ok"] is False and r["error_kind"] == "bad_input"
    assert "find_stores" in r["error"]


def test_set_store_survives_restart(client, store, site):
    import httpx

    from winelab_mcp.client import WinelabClient

    assert client.select_store("M487")["ok"] is True
    reborn = WinelabClient(transport=httpx.MockTransport(site.handler), store=store)
    try:
        assert reborn.current_pos_name() == "M487"
    finally:
        reborn.close()


def test_store_address_from_nested_field():
    """/store-finder кладёт улицу в address.line1, а не в корень объекта."""
    nested = {
        "name": "M735",
        "address": {"town": "г. Москва", "line1": "ул. Адмирала Лазарева, д. 63, к. 1"},
    }
    assert slim_store(nested)["address"] == "г. Москва, ул. Адмирала Лазарева, д. 63, к. 1"


def test_promotions_are_clean():
    raw = {
        "potentialPromotions": [
            None,
            {"description": "Скидка 10% по промокоду"},
            {"description": "Скидка 10% по промокоду"},
            {"description": "???????? ?????: ?????????? ??????"},
            {"title": "DECEMBER10"},
            {"description": "   "},
        ]
    }
    assert _promotions(raw) == ["Скидка 10% по промокоду", "DECEMBER10"]
