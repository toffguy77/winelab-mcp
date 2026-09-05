"""Вход по SMS, персистентность сессии и восстановление кук."""

from __future__ import annotations

import httpx
import pytest

from winelab_mcp import server as server_mod
from winelab_mcp.client import AuthError, WinelabClient, normalize_phone
from winelab_mcp.session import SessionStore

from .conftest import CSRF, PHONE, SMS_CODE


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+7 (999) 123-45-67", "9991234567"),
        ("8 999 123 45 67", "9991234567"),
        ("79991234567", "9991234567"),
        ("9991234567", "9991234567"),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "123", "+7 999 123 45 6789", "не телефон"])
def test_normalize_phone_rejects_junk(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_csrf_read_from_acc_config(client, site):
    """Регрессия: на живом сайте токен лежит в ACC.config, а не в <meta name=_csrf>."""
    assert client.csrf_token() == CSRF


def test_csrf_falls_back_to_login_popup(site, store):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text="<html>без токена</html>")
        return site.handler(request)

    c = WinelabClient(transport=httpx.MockTransport(handler), store=store)
    try:
        assert c.csrf_token() == CSRF
        assert ("GET", "/login/popup-form") in site.calls
    finally:
        c.close()


def test_cart_add_sends_csrf(client, site):
    client.cart_add("1019872", 2)
    assert f"CSRFToken={CSRF}" in site.last_body


def test_anonymous_is_not_authenticated(client):
    assert client.is_authenticated() is False


def test_phone_registered(client):
    assert client.phone_registered(PHONE) is True
    assert client.phone_registered("9990000000") is False


def test_send_code_and_login(client, site, store):
    client.send_sms_code(f"+7 {PHONE}")
    assert site.sms_sent == [PHONE]

    assert client.login_with_code(PHONE, SMS_CODE) is True
    assert client.is_authenticated() is True
    assert store.load()["phone"] == PHONE
    assert client.phone == PHONE


def test_login_with_wrong_code_fails(client, store):
    assert client.login_with_code(PHONE, "0000") is False
    assert store.load().get("phone") is None


def test_send_code_to_unknown_number_raises(client):
    with pytest.raises(AuthError):
        client.send_sms_code("9990000000")


def test_password_login(client):
    assert client.login_with_password(PHONE, "hunter2") is True


def test_password_is_never_persisted(client, store):
    client.login_with_password(PHONE, "hunter2")
    assert "hunter2" not in store.path.read_text(encoding="utf-8")


def test_logout_clears_disk_session(client, store):
    client.login_with_code(PHONE, SMS_CODE)
    assert store.path.exists()
    client.logout()
    assert not store.path.exists()
    # анонимная сессия дальше живёт как обычно, но следа аккаунта в ней нет
    assert client.is_authenticated() is False
    assert store.load().get("phone") is None
    assert PHONE not in store.path.read_text(encoding="utf-8")


def test_anonymous_client_does_not_clobber_cli_login(site, store):
    """Сервер стартовал до входа: логин из CLI не должен затираться."""
    running = WinelabClient(transport=httpx.MockTransport(site.handler), store=store)
    cli = WinelabClient(transport=httpx.MockTransport(site.handler), store=store)
    try:
        cli.login_with_code(PHONE, SMS_CODE)
        running.set_region("RU-SPE")  # любой запрос работающего сервера
        assert running._http.cookies["JSESSIONID"] != cli._http.cookies["JSESSIONID"]
        assert store.load()["phone"] == PHONE
        assert store.cookies()["JSESSIONID"] == cli._http.cookies["JSESSIONID"]
    finally:
        running.close()
        cli.close()


def test_session_survives_restart(site, store):
    first = WinelabClient(transport=httpx.MockTransport(site.handler), store=store)
    first.login_with_code(PHONE, SMS_CODE)
    first.close()

    second = WinelabClient(transport=httpx.MockTransport(site.handler), store=store)
    try:
        assert second.phone == PHONE
        assert second.is_authenticated() is True
    finally:
        second.close()


def test_region_is_persisted(client, store):
    client.set_region("RU-SPE")
    assert store.load()["region"] == "RU-SPE"


def test_env_cookies_override_stored_session(site, store, monkeypatch):
    store.save({"JSESSIONID": "from-disk"})
    monkeypatch.setenv("WINELAB_COOKIES", "JSESSIONID=from-env; currentPOS=M001")
    c = WinelabClient(transport=httpx.MockTransport(site.handler), store=store)
    try:
        assert c._http.cookies["JSESSIONID"] == "from-env"
        assert c._http.cookies["currentPOS"] == "M001"
    finally:
        c.close()


# --- инструменты сервера ------------------------------------------------


def test_auth_status_tool_anonymous(mocked):
    r = server_mod.auth_status()
    assert r["ok"] and r["authenticated"] is False and r["hint"]


def test_auth_flow_via_tools(mocked, site):
    sent = server_mod.auth_send_code(PHONE)
    assert sent["ok"] and sent["code_length"] == 4
    assert site.sms_sent == [PHONE]

    done = server_mod.auth_login(PHONE, SMS_CODE)
    assert done["ok"] and done["authenticated"] is True

    status = server_mod.auth_status()
    assert status["authenticated"] is True and status["phone"] == PHONE

    assert server_mod.auth_logout()["authenticated"] is False


def test_auth_send_code_unknown_number(mocked):
    r = server_mod.auth_send_code("9990000000")
    assert r["ok"] is False and r["error_kind"] == "auth"


def test_auth_login_wrong_code(mocked):
    r = server_mod.auth_login(PHONE, "0000")
    assert r["ok"] is False and r["error_kind"] == "auth"


def test_bad_phone_is_reported_as_bad_input(mocked):
    r = server_mod.auth_send_code("не телефон")
    assert r["ok"] is False and r["error_kind"] == "bad_input"


def test_server_client_uses_session_store():
    """Модульный клиент сервера пишет сессию на диск, а не в память."""
    assert isinstance(server_mod._client._store, SessionStore)
