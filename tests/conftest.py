"""Оффлайновый двойник winelab.ru на httpx.MockTransport.

Сеть не трогается: все тесты, кроме помеченных `live`, работают против него.
"""

from __future__ import annotations

import re

import httpx
import pytest

from winelab_mcp import server as server_mod
from winelab_mcp.client import WinelabClient
from winelab_mcp.session import SessionStore

CSRF = "tok-123"
PHONE = "9991234567"
SMS_CODE = "4242"

PRODUCT = {
    "code": "1019872",
    "name": "Виски Dewar's White Label 0,7 л",
    "url": "/product/1019872",
    "price": {"currencyIso": "RUB", "value": 1399.99, "priceTagColor": "Желтый ценник"},
    "stock": {"stockLevel": 7},
    "availableInOtherPoses": 1160,
    "averageRating": 4.56,
    "numberOfReviews": 25,
    "brand": "Dewar's",
    "country": "Шотландия",
    "alcoholContent": "40",
    "hit": True,
    "images": [{"url": "/medias/dewars.png"}],
    "classifications": [
        {"features": [{"name": "Объем", "featureValues": [{"value": "0.7"}]}]}
    ],
}
CHEAP = dict(
    PRODUCT,
    code="1001014",
    name="Виски Fox & Dogs 0,7 л",
    price={"value": 1059.99},
    hit=False,
    averageRating=4.1,
)

STORE = {
    "displayName": "M735",
    "name": "M735",
    "phone": "8003017755",
    "town": "г. Москва",
    "line1": "ул. Адмирала Лазарева, д. 63, к. 1",
    "line2": "",
    "openings": {"Пн": "10:00 - 23:00"},
    "formattedDistance": "",
}

# Главная отдаёт токен внутри JSON-конфига ACC.config — ровно так, как живой сайт.
HOME_HTML = (
    '<html><script>ACC.config = {"holderImg":"/x.jpg",'
    f'"CSRFToken":"{CSRF}","language":"ru"}};</script></html>'
)


class FakeSite:
    """Держит состояние: авторизацию, отправленные коды, содержимое корзины."""

    def __init__(self) -> None:
        self.authenticated = False
        self.sms_sent: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.last_body: str = ""
        self._sid_seq = 0
        # Как на живом сайте: магазин сессии живёт в куке currentPOS, а ручки
        # /store-finder/pos и /store-pickup/pos режутся на периметре (405).
        self.default_pos = "M735"
        self.pos_cookie_works = True

    def _current_pos(self, request: httpx.Request) -> str:
        if self.pos_cookie_works:
            seen = re.search(r"currentPOS=([^;]+)", request.headers.get("cookie", ""))
            if seen:
                return seen.group(1)
        return self.default_pos

    def _session_cookie(self, request: httpx.Request) -> str:
        """Как настоящий сервер: клиенту без куки выдаём новый JSESSIONID."""
        seen = re.search(r"JSESSIONID=([^;]+)", request.headers.get("cookie", ""))
        if seen:
            return seen.group(1)
        self._sid_seq += 1
        return f"sid-{self._sid_seq}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        body = request.content.decode() if request.content else ""
        self.last_body = body
        params = request.url.params

        if path == "/":
            sid = self._session_cookie(request)
            return httpx.Response(
                200,
                text=HOME_HTML,
                headers={"set-cookie": f"JSESSIONID={sid}; Path=/"},
            )
        if path == "/login/popup-form":
            return httpx.Response(
                200, text=f'<form><input name="CSRFToken" value="{CSRF}" /></form>'
            )
        if path == "/store-finder/region":
            return httpx.Response(200, text="<html>ok</html>")

        # --- авторизация ---
        if path == "/authentication/status":
            return httpx.Response(200 if self.authenticated else 401, text="")
        if path == "/login/check":
            registered = f"mobileNumber={PHONE}" in body
            return httpx.Response(200, text="true" if registered else "false")
        if path == "/confirmation/sendByPhone":
            number = params.get("number", "")
            if number != PHONE:
                return httpx.Response(400, text="")
            self.sms_sent.append(number)
            return httpx.Response(200, json={"codeLength": 4, "timeout": 60})
        if path == "/confirmation/getByPhone":
            return httpx.Response(200, json={"codeLength": 4, "secondsLeft": 42})
        if path == "/j_spring_security_check":
            ok = f"j_username={PHONE}" in body and (
                f"j_password={SMS_CODE}" in body or "j_password=hunter2" in body
            )
            self.authenticated = ok
            return httpx.Response(200, text="<html>redirect</html>")
        if path == "/logout":
            self.authenticated = False
            return httpx.Response(200, text="<html>bye</html>")

        # --- каталог ---
        if path == "/search/results":
            page = int(params.get("page", 0))
            q = params.get("q", "")
            results = [PRODUCT, CHEAP] if page == 0 else []
            if q.startswith("1019872"):
                results = [PRODUCT]
            return httpx.Response(
                200,
                json={
                    "results": results,
                    "pagination": {"totalNumberOfResults": 2, "numberOfPages": 1},
                },
            )
        if path == "/search/facets":
            return httpx.Response(
                200,
                json={
                    "facets": [
                        {
                            "code": "countryfiltr",
                            "name": "СТРАНА",
                            "values": [{"code": "Шотландия", "count": 281}],
                        }
                    ]
                },
            )
        if path == "/search/autocomplete/SearchBox":
            return httpx.Response(200, json={"products": [PRODUCT]})
        if path == "/search/taps":
            return httpx.Response(200, json=[{"relatedSearch": "виски односолодовый"}])

        # --- регионы и магазины ---
        if path == "/store-finder/getAllRegionsAndCities":
            return httpx.Response(200, json={"regions": [{"RU-MOW": "Москва и область"}]})
        if path == "/view/POSSelectorComponentController/json":
            return httpx.Response(
                200,
                json={
                    "favouritePosName": self._current_pos(request),
                    "shortRegion": "MOW",
                    "formattedAddress": "г. Москва, ...",
                    "posTimezone": 3,
                    "metroStations": [],
                },
            )
        if path == "/store-finder/pos-name":
            return httpx.Response(200, text="Вы находитесь в М735")
        if path in ("/store-finder/pos", "/store-pickup/pos"):
            return httpx.Response(405, text="<html>method not allowed</html>")
        if path == "/store-finder":
            return httpx.Response(200, json={"total": 1, "pages": 1, "data": [STORE]})
        if path.startswith("/stores/"):
            return httpx.Response(200, json=STORE)

        # --- корзина ---
        if path == "/cart/truncated/":
            return httpx.Response(
                200, text='{"items": [{"id":"1019872","count":1,"price":1399.99}]}'
            )
        if path == "/store-pickup/cart/add":
            assert f"CSRFToken={CSRF}" in body, "CSRF-токен не подставлен"
            return httpx.Response(200, json={"success": True, "bindingErrorMessages": {}})

        if path == "/blocked":
            return httpx.Response(
                401, text='<html><script src="/__qrator/qauth.js"></script></html>'
            )
        return httpx.Response(404, text="<html>not found</html>")


@pytest.fixture
def site() -> FakeSite:
    return FakeSite()


@pytest.fixture
def store(tmp_path, monkeypatch) -> SessionStore:
    """Сессия пишется во временный каталог, а не в профиль пользователя."""
    monkeypatch.setenv("WINELAB_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("WINELAB_SESSION", raising=False)
    monkeypatch.delenv("WINELAB_COOKIES", raising=False)
    return SessionStore(tmp_path / "session.json")


@pytest.fixture
def client(site, store) -> WinelabClient:
    c = WinelabClient(transport=httpx.MockTransport(site.handler), store=store)
    yield c
    c.close()


@pytest.fixture
def mocked(client, monkeypatch) -> WinelabClient:
    """Подменяет клиент, которым пользуются инструменты сервера."""
    monkeypatch.setattr(server_mod, "_client", client)
    return client


def pytest_collection_modifyitems(config, items):
    """Тесты с меткой `live` ходят в настоящий winelab.ru — только по запросу."""
    import os

    if os.environ.get("WINELAB_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="нужен WINELAB_LIVE=1 (ходит в настоящий winelab.ru)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
