"""MCP-сервер к winelab.ru (ВинЛаб).

Работает поверх недокументированного JSON API веб-версии магазина.
Запуск: `winelab-mcp` или `python -m winelab_mcp` (stdio).
"""

from __future__ import annotations

import os
from typing import Any, Literal

try:  # официальный SDK
    from mcp.server.fastmcp import FastMCP
except ImportError:  # standalone fastmcp>=2
    from fastmcp import FastMCP  # type: ignore

from .client import AuthError, BlockedError, WinelabClient, build_query
from .session import SessionStore
from .shape import BASE_URL, full_product, slim_product, slim_store

mcp = FastMCP("winelab")
_client = WinelabClient(store=SessionStore())

MAX_PAGES = int(os.environ.get("WINELAB_MAX_PAGES", "5"))
SORTS = {
    "relevance": None,
    "price_asc": ("price", False),
    "price_desc": ("price", True),
    "rating_desc": ("rating", True),
    "name_asc": ("name", False),
}


def _err(exc: Exception) -> dict:
    if isinstance(exc, BlockedError):
        kind = "blocked"
    elif isinstance(exc, (AuthError, ValueError)):
        kind = "auth" if isinstance(exc, AuthError) else "bad_input"
    else:
        kind = "error"
    return {"ok": False, "error_kind": kind, "error": str(exc)}


def _collect(q: str, pages: int) -> tuple[list[dict], int]:
    """Тянет несколько страниц выдачи (по 21 товару)."""
    items: list[dict] = []
    total = 0
    seen: set[str] = set()
    for page in range(max(1, pages)):
        data = _client.search(q, page=page)
        total = (data.get("pagination") or {}).get("totalNumberOfResults", total)
        chunk = data.get("results") or []
        if not chunk:
            break
        for p in chunk:
            code = p.get("code")
            if code and code not in seen:
                seen.add(code)
                items.append(p)
        n_pages = (data.get("pagination") or {}).get("numberOfPages", 1)
        if page + 1 >= n_pages:
            break
    return items, total


@mcp.tool()
def search_products(
    query: str,
    limit: int = 20,
    in_stock_only: bool = False,
    min_price: float | None = None,
    max_price: float | None = None,
    country: str | None = None,
    brand: str | None = None,
    alcohol_type: str | None = None,
    volume: str | None = None,
    sort: Literal[
        "relevance", "price_asc", "price_desc", "rating_desc", "name_asc"
    ] = "relevance",
    pages: int = 2,
) -> dict:
    """Поиск товаров в каталоге ВинЛаб.

    query — свободный текст ("виски односолодовый", "Jack Daniel's") либо артикул.
    in_stock_only — только то, что есть в текущем магазине («Забрать сегодня»).
    country / brand / alcohol_type / volume — значения фасетов, ровно как их
    возвращает list_filters (напр. country="Шотландия", volume="0.7").
    Сортировка и ценовой диапазон применяются на стороне клиента к выбранным
    страницам выдачи (pages × 21 товар), т.к. сервер их игнорирует.
    """
    try:
        filters: list[tuple[str, str]] = []
        if in_stock_only:
            filters.append(("inStock", "true"))
        if country:
            filters.append(("countryfiltr", country))
        if brand:
            filters.append(("brands", brand))
        if alcohol_type:
            filters.append(("alcoholtype", alcohol_type))
        if volume:
            filters.append(("Capacity", volume))

        need_scan = sort != "relevance" or min_price is not None or max_price is not None
        pages = max(1, min(pages if need_scan else 1, MAX_PAGES))

        raw, total = _collect(build_query(query, "relevance", filters), pages)
        items = [slim_product(p) for p in raw]

        if min_price is not None:
            items = [i for i in items if (i["price"] or 0) >= min_price]
        if max_price is not None:
            items = [i for i in items if (i["price"] or 0) <= max_price]

        spec = SORTS.get(sort)
        if spec:
            key, reverse = spec
            items.sort(
                key=lambda i: (
                    i.get(key) is None,
                    i.get(key) or (0 if key != "name" else ""),
                ),
                reverse=reverse,
            )

        return {
            "ok": True,
            "query": query,
            "total_found": total,
            "scanned": len(raw),
            "returned": min(limit, len(items)),
            "note": "total_found — по всей выдаче; фильтр цены и сортировка применены к scanned",
            "items": items[:limit],
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def product_details(code: str) -> dict:
    """Полная карточка товара по артикулу (напр. "1019872").

    Страница /product/<code> закрыта антиботом, поэтому данные берутся
    из поисковой выдачи по артикулу.
    """
    try:
        data = _client.search(build_query(code))
        match = next(
            (p for p in (data.get("results") or []) if str(p.get("code")) == str(code)),
            None,
        )
        if not match:
            return {"ok": False, "error": f"товар {code} не найден в текущем регионе"}
        return {"ok": True, "product": full_product(match)}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def suggest(term: str, limit: int = 8) -> dict:
    """Подсказки поиска: похожие товары и уточняющие запросы."""
    try:
        ac = _client.autocomplete(term)
        products = [slim_product(p) for p in (ac.get("products") or [])[:limit]]
        try:
            taps = [t.get("relatedSearch") for t in (_client.taps(term) or [])]
        except Exception:  # noqa: BLE001
            taps = []
        return {"ok": True, "products": products, "related_queries": taps}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def list_filters(query: str, top: int = 12) -> dict:
    """Доступные фасеты (бренд, страна, тип, объём, выдержка, скидки) для запроса.

    Значения отсюда можно передавать в search_products.
    """
    try:
        data = _client.facets(query)
        out = []
        for f in data.get("facets") or []:
            values = [
                {"value": v.get("code"), "count": v.get("count")}
                for v in (f.get("values") or [])[:top]
            ]
            out.append(
                {
                    "code": f.get("code"),
                    "name": (f.get("name") or "").split(":::")[0].strip(),
                    "values_total": len(f.get("values") or []),
                    "values": values,
                }
            )
        return {"ok": True, "query": query, "facets": out}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def list_regions() -> dict:
    """Регионы присутствия сети (код + название). Код нужен для set_region."""
    try:
        data = _client.regions()
        regions = []
        for item in data.get("regions") or []:
            for code, name in item.items():
                regions.append({"code": code, "name": name})
        return {"ok": True, "current": _client.region, "regions": regions}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def set_region(code: str) -> dict:
    """Сменить регион (напр. "RU-SPE"). Влияет на цены, остатки и список магазинов."""
    try:
        _client.set_region(code)
        pos = _client.current_pos()
        return {
            "ok": True,
            "region": code,
            "current_store": pos.get("favouritePosName"),
            "address": pos.get("formattedAddress"),
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def current_store() -> dict:
    """Магазин, к которому сейчас привязана сессия (от него зависят цены и остатки)."""
    try:
        pos = _client.current_pos()
        return {
            "ok": True,
            "code": pos.get("favouritePosName"),
            "address": pos.get("formattedAddress"),
            "region": pos.get("shortRegion"),
            "timezone": pos.get("posTimezone"),
            "metro": [m.get("name") for m in (pos.get("metroStations") or [])],
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def set_store(store_code: str) -> dict:
    """Привязать сессию к конкретному магазину ("M735") — от него зависят остатки.

    Код магазина берётся из find_stores. Магазин должен быть в текущем регионе:
    если он в другом, сначала переключите регион через set_region.
    """
    try:
        card = _client.store(store_code)
    except BlockedError:
        return {
            "ok": False,
            "error_kind": "bad_input",
            "error": f"магазин {store_code} не найден в регионе {_client.region} — "
            "проверьте код через find_stores или смените регион через set_region",
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc)

    try:
        result = _client.select_store(store_code)
        if not result["ok"]:
            return {
                "ok": False,
                "error_kind": "unsupported",
                "error": f"сайт не дал переключиться на {store_code}: ни одна из "
                f"{len(result['attempts'])} стратегий не прижилась",
                "attempts": result["attempts"],
                "current_store": _client.current_pos_name(),
            }
        return {
            "ok": True,
            "store": slim_store(card),
            "method": result["method"],
            "note": "остатки и цены в поиске теперь считаются по этому магазину; "
            "магазин сохранён в сессии и переживёт перезапуск сервера",
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def find_stores(query: str | None = None, limit: int = 10, page: int = 0) -> dict:
    """Магазины текущего региона; query — часть адреса или города ("Адмирала", "Москва")."""
    try:
        data = _client.stores(query, page=page)
        return {
            "ok": True,
            "total": data.get("total"),
            "pages": data.get("pages"),
            "page": page,
            "stores": [slim_store(s) for s in (data.get("data") or [])[:limit]],
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def store_details(store_code: str) -> dict:
    """Карточка магазина по коду ("M735"): адрес, часы работы, метро."""
    try:
        return {"ok": True, "store": slim_store(_client.store(store_code))}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cart_view() -> dict:
    """Корзина текущей сессии сервера."""
    try:
        cart = _client.cart()
        items = cart.get("items") or []
        enriched = []
        for it in items:
            code = str(it.get("id"))
            name = None
            try:
                res = _client.search(build_query(code))
                match = next(
                    (
                        p
                        for p in (res.get("results") or [])
                        if str(p.get("code")) == code
                    ),
                    None,
                )
                name = (match or {}).get("name")
            except Exception:  # noqa: BLE001
                pass
            enriched.append(
                {
                    "code": code,
                    "name": name,
                    "qty": it.get("count"),
                    "price": it.get("price"),
                }
            )
        return {
            "ok": True,
            "items": enriched,
            "total": sum((i["price"] or 0) * (i["qty"] or 0) for i in enriched),
            "url": f"{BASE_URL}/cart",
            "note": "корзина живёт в сессии этого сервера; чтобы она была общей с сайтом, "
            "войдите в аккаунт (auth_send_code / auth_login)",
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def cart_add(code: str, qty: int = 1) -> dict:
    """Добавить товар в корзину (артикул + количество)."""
    try:
        res: Any = _client.cart_add(code, qty)
        ok = bool(res.get("success")) if isinstance(res, dict) else True
        return {
            "ok": ok,
            "code": code,
            "qty": qty,
            "cart_url": f"{BASE_URL}/cart",
            "errors": (res or {}).get("bindingErrorMessages")
            if isinstance(res, dict)
            else None,
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# --- авторизация --------------------------------------------------------
#
# Вход по одноразовому SMS-коду: пароль в переписку не попадает.
# Пароль поддерживается только в CLI (`winelab-mcp login --password`).


@mcp.tool()
def auth_status() -> dict:
    """Вошли ли мы в аккаунт ВинЛаб и под каким номером."""
    try:
        logged_in = _client.is_authenticated()
        return {
            "ok": True,
            "authenticated": logged_in,
            "phone": _client.phone if logged_in else None,
            "region": _client.region,
            "hint": None
            if logged_in
            else "войдите: auth_send_code(телефон) → auth_login(телефон, код из SMS)",
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def auth_send_code(phone: str) -> dict:
    """Отправить SMS с одноразовым кодом на номер (в любом формате: +7…, 8…, 9…).

    Дальше передайте код в auth_login. Пароль от аккаунта вводить не нужно.
    """
    try:
        if not _client.phone_registered(phone):
            return {
                "ok": False,
                "error_kind": "auth",
                "error": "на этот номер нет аккаунта ВинЛаб — зарегистрируйтесь на сайте",
            }
        info = _client.send_sms_code(phone)
        return {
            "ok": True,
            "phone": phone,
            "code_length": info.get("codeLength") or info.get("length"),
            "retry_after_sec": info.get("timeout") or info.get("secondsLeft"),
            "next": "вызовите auth_login(phone, code) с кодом из SMS",
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def auth_login(phone: str, code: str) -> dict:
    """Завершить вход: номер + код из SMS. Сессия сохраняется на диск."""
    try:
        if _client.login_with_code(phone, code):
            return {
                "ok": True,
                "authenticated": True,
                "phone": _client.phone,
                "note": "сессия сохранена, переживёт перезапуск сервера",
            }
        return {
            "ok": False,
            "error_kind": "auth",
            "error": "код не подошёл или истёк — запросите новый через auth_send_code",
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def auth_logout() -> dict:
    """Выйти из аккаунта и стереть сохранённую сессию с диска."""
    try:
        _client.logout()
        return {"ok": True, "authenticated": False}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def main() -> None:
    from .cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
