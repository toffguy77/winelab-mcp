"""HTTP-клиент к внутреннему JSON API winelab.ru.

API недокументированный, снят с веб-версии (SAP Hybris). Все эндпоинты — в API.md.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from .session import SessionStore, parse_cookie_header

BASE = os.environ.get("WINELAB_BASE", "https://www.winelab.ru")
DEFAULT_REGION = os.environ.get("WINELAB_REGION", "RU-MOW")
UA = os.environ.get(
    "WINELAB_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
)

# Токен живёт в трёх разных местах в зависимости от страницы: в JSON-конфиге
# ACC.config (главная), в meta и в скрытом поле формы (попапы логина).
CSRF_PATTERNS = (
    re.compile(r'name="_csrf"\s+content="([^"]+)"'),
    re.compile(r'"CSRFToken"\s*:\s*"([^"]+)"'),
    re.compile(r'name="CSRFToken"\s+value="([^"]+)"'),
)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class WinelabError(RuntimeError):
    pass


class BlockedError(WinelabError):
    """Ответ пришёл HTML-ом: антибот Qrator, 404 или редирект на страницу."""


class AuthError(WinelabError):
    """Не удалось авторизоваться: неверный код/пароль или номер не зарегистрирован."""


def normalize_phone(raw: str) -> str:
    """'+7 (999) 123-45-67' -> '9991234567'. Сайт ждёт 10 цифр без кода страны."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in ("7", "8"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(
            f"ожидался российский номер из 10 цифр (получено {len(digits)}): {raw!r}"
        )
    return digits


class WinelabClient:
    """Ленивая сессия: куки JSESSIONID / currentRegion / currentPOS + CSRF-токен."""

    def __init__(
        self,
        base: str = BASE,
        region: str | None = DEFAULT_REGION,
        transport: httpx.BaseTransport | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self.base = base.rstrip("/")
        self.region = region
        self._domain = urlsplit(self.base).hostname or "localhost"
        self._csrf: str | None = None
        self._ready = False
        self._lock = threading.RLock()
        self._store = store
        self._http = httpx.Client(
            base_url=self.base,
            timeout=httpx.Timeout(25.0, connect=10.0),
            follow_redirects=True,
            transport=transport,
            headers={
                "User-Agent": UA,
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Referer": self.base + "/",
            },
        )
        self._restore_cookies()

    # --- сессия ---------------------------------------------------------

    def _restore_cookies(self) -> None:
        """Сохранённая сессия, поверх неё — куки из WINELAB_COOKIES."""
        saved: dict[str, str] = {}
        if self._store is not None:
            data = self._store.load()
            saved.update(self._store.cookies())
            if not self.region and data.get("region"):
                self.region = data["region"]
        env = os.environ.get("WINELAB_COOKIES", "").strip()
        if env:
            saved.update(parse_cookie_header(env))
        for name, value in saved.items():
            self._http.cookies.set(name, value, domain=self._domain)

    def _cookie_pairs(self) -> dict[str, str]:
        """Куки как {имя: значение}.

        `dict(client.cookies)` тут нельзя: httpx.Cookies — Mapping, и его
        `__getitem__` бросает CookieConflict, если одно имя пришло сразу для
        нескольких доменов или путей (сайт ставит `currentRegion` и на
        `www.winelab.ru`, и на `.winelab.ru`). Идём по банке напрямую, отдавая
        предпочтение куке нашего домена.
        """
        out: dict[str, str] = {}
        preferred: dict[str, str] = {}
        for cookie in self._http.cookies.jar:
            name, value = cookie.name, cookie.value or ""
            out[name] = value
            if (cookie.domain or "").lstrip(".") == self._domain:
                preferred[name] = value
        out.update(preferred)
        return out

    def _drop_cookie(self, name: str) -> None:
        """Убрать куку во всех доменах и путях, где она успела завестись."""
        jar = self._http.cookies.jar
        for cookie in list(jar):
            if cookie.name == name:
                try:
                    jar.clear(cookie.domain, cookie.path, cookie.name)
                except KeyError:
                    pass

    def _persist(self) -> None:
        """Сохранить сессию, не затирая чужую авторизованную.

        MCP-сервер и `winelab-mcp login` в соседнем терминале смотрят в один
        файл. Если на диске лежит сессия с привязанным телефоном, а у нас в
        руках другая (мы стартовали до входа) — не трогаем её: наш аноним
        обесценил бы вход, сделанный из CLI.
        """
        if self._store is None:
            return
        stored = self._store.load()
        ours = self._cookie_pairs()
        stored_sid = (stored.get("cookies") or {}).get("JSESSIONID")
        if stored.get("phone") and stored_sid != ours.get("JSESSIONID"):
            return
        self._store.save(ours, region=self.region, phone=stored.get("phone"))

    @property
    def phone(self) -> str | None:
        return self._store.load().get("phone") if self._store is not None else None

    # --- инфраструктура -------------------------------------------------

    def _bootstrap(self) -> None:
        with self._lock:
            if self._ready:
                return
            r = self._http.get("/", headers={"Accept": "text/html"})
            self._csrf = _find_csrf(r.text)
            if self.region:
                # смена региона меняет цены, остатки и список магазинов
                self._http.get(
                    "/store-finder/region",
                    params={"code": self.region},
                    headers=self._ajax_headers(),
                )
            self._ready = True
            self._persist()

    def _ajax_headers(self) -> dict[str, str]:
        return {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }

    def csrf_token(self) -> str | None:
        """Токен для мутаций; попап логина отдаёт его надёжнее главной страницы."""
        self._bootstrap()
        if self._csrf:
            return self._csrf
        with self._lock:
            r = self._http.get("/login/popup-form", headers=self._ajax_headers())
            self._csrf = _find_csrf(r.text)
        return self._csrf

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        retries: int = 2,
    ) -> httpx.Response:
        self._bootstrap()
        headers = self._ajax_headers()
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = self._http.request(
                    method, path, params=params, data=data, headers=headers
                )
            except httpx.HTTPError as exc:  # сеть/таймаут
                last = exc
                time.sleep(0.6 * (attempt + 1))
                continue
            if r.status_code in RETRY_STATUSES and attempt < retries:
                time.sleep(0.8 * (attempt + 1))
                continue
            return r
        raise WinelabError(f"{method} {path}: сеть недоступна ({last})")

    def get_json(self, path: str, params: dict | None = None) -> Any:
        r = self._request("GET", path, params=params)
        return self._as_json(r, path)

    def post_json(self, path: str, data: dict) -> Any:
        r = self._post_with_csrf(path, data)
        return self._as_json(r, path)

    def _post_with_csrf(self, path: str, data: dict) -> httpx.Response:
        payload = dict(data)
        token = self.csrf_token()
        if token:
            payload["CSRFToken"] = token
        r = self._request("POST", path, data=payload)
        if r.status_code in (403, 405) and token:
            # токен протух — перечитываем страницу и повторяем один раз
            with self._lock:
                self._ready = False
                self._csrf = None
            token = self.csrf_token()
            payload = dict(data)
            if token:
                payload["CSRFToken"] = token
            r = self._request("POST", path, data=payload)
        return r

    @staticmethod
    def _as_json(r: httpx.Response, path: str) -> Any:
        text = r.text.lstrip()
        if text.startswith("<"):
            if "__qrator" in text[:1000] or "qrator" in text[:1000].lower():
                raise BlockedError(
                    f"{path}: запрос заблокирован антиботом Qrator "
                    "(этот путь требует прохождения JS-челленджа в браузере)"
                )
            raise BlockedError(
                f"{path}: сервер вернул HTML вместо JSON (HTTP {r.status_code}) — "
                "скорее всего неверный путь/параметры или пустая выборка"
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise WinelabError(f"{path}: не разобрать ответ ({exc})") from exc

    # --- каталог --------------------------------------------------------

    def search(self, q: str, page: int = 0) -> dict:
        """q — hybris-строка вида 'виски:relevance:inStock:true'."""
        return self.get_json("/search/results", {"q": q, "page": page})

    def facets(self, text: str) -> dict:
        return self.get_json("/search/facets", {"text": text})

    def autocomplete(self, term: str) -> dict:
        return self.get_json("/search/autocomplete/SearchBox", {"term": term})

    def taps(self, term: str) -> list:
        return self.get_json("/search/taps", {"term": term})

    # --- регионы и магазины ---------------------------------------------

    def regions(self) -> dict:
        return self.get_json("/store-finder/getAllRegionsAndCities")

    def set_region(self, code: str) -> None:
        self._bootstrap()
        self._http.get(
            "/store-finder/region", params={"code": code}, headers=self._ajax_headers()
        )
        self.region = code
        self._persist()

    def current_pos(self) -> dict:
        return self.get_json("/view/POSSelectorComponentController/json")

    def current_pos_name(self) -> str | None:
        try:
            return self.current_pos().get("favouritePosName")
        except WinelabError:
            return None

    def select_store(self, code: str) -> dict:
        """Привязать сессию к конкретному магазину региона.

        Штатной ручки у сайта нет: фронт зовёт `POST /store-finder/pos`, который
        режется на периметре (405). Поэтому пробуем несколько способов подряд и
        после каждого сверяемся с `/view/POSSelectorComponentController/json`.
        Последний способ — выставить куку `currentPOS` руками: состояние магазина
        сайт держит именно в ней.

        Возвращает {"ok", "method", "attempts"}; сеть трогаем ровно до первой
        сработавшей стратегии.
        """
        self._bootstrap()
        code = (code or "").strip()
        if not code:
            raise ValueError("нужен код магазина, напр. 'M735'")

        attempts: list[dict[str, Any]] = []
        for name, apply in self._pos_strategies():
            error: str | None = None
            try:
                apply(code)
            except Exception as exc:  # noqa: BLE001 — стратегия неудачна, идём дальше
                error = str(exc)[:200]
            current = self.current_pos_name()
            attempts.append({"method": name, "pos_after": current, "error": error})
            if current == code:
                self._persist()
                return {"ok": True, "method": name, "attempts": attempts}

        # ни один способ не прижился — откатываем куку, чтобы не врать о магазине
        self._drop_cookie("currentPOS")
        return {"ok": False, "method": None, "attempts": attempts}

    def _pos_strategies(self):
        ajax = self._ajax_headers()

        def get_pos_name(code: str) -> None:
            self._http.get("/store-finder/pos-name", params={"posName": code}, headers=ajax)

        def get_pos(code: str) -> None:
            self._http.get("/store-finder/pos", params={"posName": code}, headers=ajax)

        def post_pos(code: str) -> None:
            self._post_with_csrf("/store-finder/pos", {"storeId": code})

        def post_pickup(code: str) -> None:
            self._post_with_csrf("/store-pickup/pos", {"posName": code})

        def set_cookie(code: str) -> None:
            # сначала выметаем чужие currentPOS: иначе на разных доменах
            # окажется две куки и сайт возьмёт не нашу
            self._drop_cookie("currentPOS")
            self._http.cookies.set("currentPOS", code, domain=self._domain, path="/")

        return (
            ("store-finder/pos-name", get_pos_name),
            ("store-finder/pos (GET)", get_pos),
            ("store-finder/pos (POST)", post_pos),
            ("store-pickup/pos (POST)", post_pickup),
            ("cookie currentPOS", set_cookie),
        )

    def stores(self, q: str | None = None, page: int = 0) -> dict:
        params: dict[str, Any] = {"page": page}
        if q:
            params["q"] = q
        return self.get_json("/store-finder", params)

    def store(self, pos_name: str) -> dict:
        return self.get_json(f"/stores/{quote(pos_name)}/json")

    # --- корзина --------------------------------------------------------

    def cart(self) -> dict:
        return self.get_json("/cart/truncated/")

    def cart_add(self, code: str, qty: int = 1) -> dict:
        result = self.post_json(
            "/store-pickup/cart/add",
            {"productCodePost": code, "qtyPost": str(qty)},
        )
        self._persist()
        return result

    # --- авторизация ----------------------------------------------------

    def is_authenticated(self) -> bool:
        """`/authentication/status`: 200 — вошли, 401 — аноним."""
        r = self._request("GET", "/authentication/status", retries=0)
        return r.status_code == 200

    def phone_registered(self, phone: str) -> bool:
        """`/login/check` -> true, если на номер заведён аккаунт."""
        r = self._post_with_csrf("/login/check", {"mobileNumber": normalize_phone(phone)})
        return r.text.strip().lower() == "true"

    def send_sms_code(self, phone: str) -> dict:
        """Просит сайт отправить SMS с одноразовым кодом.

        GET — как во фронте; если ручку перевели на POST или прикрыли CSRF'ом,
        пробуем ещё раз POST'ом, прежде чем сдаваться.
        """
        number = normalize_phone(phone)
        r = self._request("GET", "/confirmation/sendByPhone", params={"number": number})
        if r.status_code < 400:
            return _maybe_json(r)
        fallback = self._post_with_csrf("/confirmation/sendByPhone", {"number": number})
        if fallback.status_code < 400:
            return _maybe_json(fallback)
        raise AuthError(
            f"не удалось отправить код на {number}: "
            f"GET — {_describe(r)}, POST — {_describe(fallback)}"
        )

    def sms_code_info(self, phone: str) -> dict:
        """Сколько ждать до повторной отправки и сколько цифр в коде."""
        r = self._request(
            "GET", "/confirmation/getByPhone", params={"number": normalize_phone(phone)}
        )
        return _maybe_json(r)

    def login_with_code(self, phone: str, code: str) -> bool:
        return self._spring_login(phone, code, "PHONE")

    def login_with_password(self, phone: str, password: str) -> bool:
        return self._spring_login(phone, password, "PASSWORD")

    def _spring_login(self, phone: str, secret: str, auth_type: str) -> bool:
        number = normalize_phone(phone)
        token = self.csrf_token()
        payload = {
            "j_username": number,
            "j_password": secret,
            "authType": auth_type,
            "isCheckout": "false",
        }
        if token:
            payload["CSRFToken"] = token
        self._request("POST", "/j_spring_security_check", data=payload, retries=0)
        ok = self.is_authenticated()
        if ok and self._store is not None:
            self._store.save(self._cookie_pairs(), region=self.region, phone=number)
        return ok

    def logout(self) -> None:
        try:
            self._request("GET", "/logout", retries=0)
        except WinelabError:
            pass
        self._http.cookies.clear()
        with self._lock:
            self._ready = False
            self._csrf = None
        if self._store is not None:
            self._store.clear()

    def close(self) -> None:
        self._http.close()


def _find_csrf(html: str) -> str | None:
    for pattern in CSRF_PATTERNS:
        m = pattern.search(html)
        if m:
            return m.group(1)
    return None


def _describe(r: httpx.Response) -> str:
    """Короткий разбор неудачного ответа — чтобы не гадать по одному лишь коду."""
    body = (r.text or "").strip()
    if "qrator" in body[:1000].lower():
        return f"HTTP {r.status_code}, антибот Qrator"
    if r.status_code == 429:
        return f"HTTP {r.status_code}, лимит попыток — подождите пару минут"
    snippet = " ".join(body[:160].split())
    return f"HTTP {r.status_code}" + (f", тело: {snippet!r}" if snippet else ", пустое тело")


def _maybe_json(r: httpx.Response) -> dict:
    try:
        data = json.loads(r.text or "{}")
    except json.JSONDecodeError:
        return {"raw": r.text[:200]}
    return data if isinstance(data, dict) else {"data": data}


def build_query(
    text: str, sort: str = "relevance", filters: Iterable[tuple[str, str]] = ()
) -> str:
    """'виски' + [('inStock','true')] -> 'виски:relevance:inStock:true'."""
    parts = [text or "", sort or "relevance"]
    for name, value in filters:
        parts.extend([str(name), str(value)])
    return ":".join(parts)
