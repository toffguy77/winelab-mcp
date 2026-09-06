"""Нормализация «толстых» hybris-объектов в компактные словари для LLM."""

from __future__ import annotations

from typing import Any

BASE_URL = "https://www.winelab.ru"


def _g(d: Any, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _classifications(product: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for cls in product.get("classifications") or []:
        for feature in cls.get("features") or []:
            name = feature.get("name")
            values = [
                str(v.get("value"))
                for v in (feature.get("featureValues") or [])
                if v.get("value") is not None
            ]
            if name and values:
                out[name] = ", ".join(values)
    return out


def _image(product: dict) -> str | None:
    for img in product.get("images") or []:
        url = img.get("url")
        if url:
            return url if url.startswith("http") else BASE_URL + url
    return None


def slim_product(p: dict) -> dict:
    """Короткая карточка для списков."""
    price = p.get("price") or {}
    return {
        "code": p.get("code"),
        "name": p.get("name"),
        "price": price.get("value"),
        "price_tag": price.get("priceTagColor"),
        "discount": price.get("valueDiscount"),
        "url": BASE_URL + (p.get("url") or f"/product/{p.get('code')}"),
        "brand": p.get("brand"),
        "country": p.get("country"),
        "alcohol": p.get("alcoholContent"),
        "rating": p.get("averageRating"),
        "reviews": p.get("numberOfReviews"),
        "stock_in_current_store": _g(p, "stock", "stockLevel"),
        "stores_with_stock": p.get("availableInOtherPoses"),
        "preorder": p.get("isPreorder"),
        "next_day": p.get("isNextDay"),
        "bonus": p.get("bonus"),
        "flags": [
            k
            for k in ("hit", "novelty", "bestOffer", "bestPrice", "superDiscount",
                      "promoOfTheDay", "productOfTheDay", "doubleBonus")
            if p.get(k)
        ],
    }


def full_product(p: dict) -> dict:
    """Развёрнутая карточка товара."""
    out = slim_product(p)
    out.update(
        {
            "description": p.get("description") or p.get("summary"),
            "ean": p.get("ean"),
            "manufacturer": p.get("winLabManufacturer") or p.get("manufacturer"),
            "categories": [c.get("name") for c in (p.get("categories") or []) if c.get("name")],
            "attributes": _classifications(p),
            "image": _image(p),
            "max_qty": p.get("maxQty"),
            "promotions": _promotions(p),
            "sommelier": p.get("sommelier"),
            "consumption": p.get("consumption"),
            "rating_color": p.get("averageColorRating"),
            "rating_taste": p.get("averageTasteRating"),
            "rating_flavor": p.get("averageFlavorRating"),
            "recommend_pct": p.get("percentagePeopleRecommended"),
        }
    )
    return {k: v for k, v in out.items() if v not in (None, [], {}, "")}


def _promotions(p: dict) -> list[str]:
    """Акции товара: без пустых, без дублей, без битой кодировки.

    `potentialPromotions` приходит списком на сотню элементов, где почти всё —
    `null`, а часть описаний бэкенд отдаёт мусором вида `???????? ?????`
    (потерянная кириллица). В выдачу такое пускать незачем.
    """
    out: list[str] = []
    seen: set[str] = set()
    for pr in p.get("potentialPromotions") or []:
        if not isinstance(pr, dict):
            continue
        text = (pr.get("description") or pr.get("title") or "").strip()
        if not text or text in seen:
            continue
        letters = [ch for ch in text if ch.isalpha()]
        if not letters or text.count("?") > len(text) / 4:
            continue  # кодировка потеряна на стороне сайта
        seen.add(text)
        out.append(text)
    return out


def slim_store(s: dict) -> dict:
    address = s.get("address") or {}
    # /store-finder кладёт улицу в address.line1, /stores/<POS>/json — в корень
    parts = [
        s.get("town") or address.get("town"),
        s.get("line1") or address.get("line1"),
        s.get("line2") or address.get("line2"),
    ]
    line = ", ".join(x for x in parts if x) or address.get("formattedAddress")
    return {
        "code": s.get("name") or s.get("displayName"),
        "address": line,
        "phone": s.get("phone"),
        "openings": s.get("openings") or _openings(s),
        "metro": [
            m.get("name")
            for m in (s.get("metroStations") or address.get("stations") or [])
            if isinstance(m, dict) and m.get("name")
        ],
        "distance": s.get("formattedDistance") or None,
        "url": f"{BASE_URL}/stores/{s.get('name') or s.get('displayName')}",
    }


def _openings(s: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for d in _g(s, "openingHours", "weekDayOpeningList", default=[]) or []:
        day = d.get("weekDay")
        if not day:
            continue
        if d.get("closed"):
            out[day] = "выходной"
        else:
            out[day] = (
                f"{_g(d, 'openingTime', 'formattedHour', default='?')} - "
                f"{_g(d, 'closingTime', 'formattedHour', default='?')}"
            )
    return out
