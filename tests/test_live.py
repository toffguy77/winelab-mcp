"""Канарейка для недокументированного API: ходит в настоящий winelab.ru.

Запуск: WINELAB_LIVE=1 pytest tests/test_live.py
По умолчанию пропускается, чтобы CI не зависел от чужого сайта и не шумел.
Аккаунт не нужен — проверяется только анонимная часть, SMS никому не шлём.
"""

from __future__ import annotations

import pytest

from winelab_mcp.client import WinelabClient

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    c = WinelabClient(store=None)  # живую сессию на диск не пишем
    yield c
    c.close()


def test_csrf_token_is_found(live):
    """Если сломается — молча отвалятся все POST (корзина, вход)."""
    assert live.csrf_token(), "CSRF-токен не найден на главной"


def test_search_returns_products(live):
    data = live.search("виски:relevance")
    assert data["results"], "пустая выдача по 'виски'"
    first = data["results"][0]
    assert first.get("code") and first.get("name")
    assert (first.get("price") or {}).get("value") is not None


def test_facets_have_known_codes(live):
    codes = {f.get("code") for f in live.facets("виски").get("facets") or []}
    assert {"countryfiltr", "brands"} & codes, f"фасеты поехали: {codes}"


def test_regions_include_moscow(live):
    codes = {c for item in live.regions()["regions"] for c in item}
    assert "RU-MOW" in codes


def test_stores_and_current_pos(live):
    stores = live.stores("Москва")
    assert stores.get("data"), "магазины не нашлись"
    assert live.current_pos().get("favouritePosName")


def test_autocomplete(live):
    assert live.autocomplete("вис").get("products") is not None


def test_anonymous_session_is_not_authenticated(live):
    assert live.is_authenticated() is False


def test_unregistered_phone_check(live):
    """`/login/check` отвечает false — значит эндпоинт входа жив. SMS не шлём."""
    assert live.phone_registered("9990000000") is False
