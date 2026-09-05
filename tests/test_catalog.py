"""Каталог, магазины, корзина — то, что работает без авторизации."""

from __future__ import annotations

import json

import pytest

from winelab_mcp import server as server_mod
from winelab_mcp.client import build_query
from winelab_mcp.shape import full_product, slim_product, slim_store

from .conftest import PRODUCT, STORE

pytestmark = pytest.mark.usefixtures("mocked")


def test_build_query():
    assert (
        build_query("виски", "relevance", [("inStock", "true")])
        == "виски:relevance:inStock:true"
    )


def test_search_sort_and_filter():
    r = server_mod.search_products("виски", sort="price_asc")
    assert r["ok"] and [i["code"] for i in r["items"]] == ["1001014", "1019872"]
    r = server_mod.search_products("виски", min_price=1200)
    assert [i["code"] for i in r["items"]] == ["1019872"]


def test_search_in_stock_builds_facet(mocked):
    seen = {}
    orig = mocked.search
    mocked.search = lambda q, page=0: (seen.update(q=q), orig(q, page))[1]
    server_mod.search_products("виски", in_stock_only=True, country="Шотландия")
    assert seen["q"] == "виски:relevance:inStock:true:countryfiltr:Шотландия"


def test_search_respects_max_pages(monkeypatch, mocked):
    monkeypatch.setattr(server_mod, "MAX_PAGES", 1)
    r = server_mod.search_products("виски", sort="price_asc", pages=99)
    assert r["ok"] and r["scanned"] == 2


def test_product_details():
    r = server_mod.product_details("1019872")
    assert r["ok"]
    p = r["product"]
    assert p["attributes"]["Объем"] == "0.7"
    assert p["image"].startswith("https://www.winelab.ru/medias/")
    assert "hit" in p["flags"]


def test_product_details_missing():
    r = server_mod.product_details("0000000")
    assert r["ok"] is False


def test_suggest():
    r = server_mod.suggest("виски")
    assert r["ok"] and r["related_queries"] == ["виски односолодовый"]


def test_stores_and_cart():
    assert server_mod.find_stores("Адмирала")["stores"][0]["code"] == "M735"
    assert server_mod.store_details("M735")["store"]["phone"] == "8003017755"
    cart = server_mod.cart_view()
    assert cart["items"][0]["name"].startswith("Виски Dewar")
    assert cart["total"] == pytest.approx(1399.99)
    assert server_mod.cart_add("1019872", 2)["ok"] is True


def test_regions_and_current_store():
    assert server_mod.list_regions()["regions"][0]["code"] == "RU-MOW"
    assert server_mod.current_store()["code"] == "M735"
    assert server_mod.list_filters("виски")["facets"][0]["code"] == "countryfiltr"


def test_qrator_error_is_explained(mocked):
    with pytest.raises(Exception) as excinfo:
        mocked.get_json("/blocked")
    r = server_mod._err(excinfo.value)
    assert r["error_kind"] == "blocked" and "Qrator" in r["error"]


def test_slim_shapes():
    assert slim_product(PRODUCT)["price"] == 1399.99
    assert slim_store(STORE)["address"].startswith("г. Москва, ул. Адмирала")
    assert json.dumps(full_product(PRODUCT), ensure_ascii=False)
