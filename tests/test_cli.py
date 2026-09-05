"""CLI: разбор аргументов, вход/выход, выбор транспорта."""

from __future__ import annotations

import httpx
import pytest

from winelab_mcp import cli
from winelab_mcp.client import WinelabClient

from .conftest import PHONE, SMS_CODE


@pytest.fixture
def cli_client(site, store, monkeypatch):
    """Все команды CLI работают с одним клиентом поверх фейкового сайта."""
    created: list[WinelabClient] = []

    def factory() -> WinelabClient:
        c = WinelabClient(transport=httpx.MockTransport(site.handler), store=store)
        created.append(c)
        return c

    monkeypatch.setattr(cli, "_client", factory)
    yield created
    for c in created:
        c.close()


def test_bare_invocation_serves(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "cmd_serve", lambda args: seen.update(vars(args)) or 0)
    assert cli.main([]) == 0
    assert seen["transport"] == "stdio"


def test_serve_transport_and_port_parsed():
    args = cli.build_parser().parse_args(
        ["serve", "--transport", "http", "--host", "0.0.0.0", "--port", "9001"]
    )
    assert (args.transport, args.host, args.port) == ("http", "0.0.0.0", 9001)


def test_transport_names_map_to_sdk():
    assert cli.TRANSPORTS == {
        "stdio": "stdio",
        "http": "streamable-http",
        "sse": "sse",
    }


def test_login_by_sms(cli_client, site, store, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_prompt", lambda text, secret=False: SMS_CODE)
    assert cli.main(["login", f"+7{PHONE}"]) == 0
    assert site.sms_sent == [PHONE]
    assert store.load()["phone"] == PHONE
    assert "вошли как" in capsys.readouterr().out


def test_login_rejects_bad_phone(cli_client, capsys):
    assert cli.main(["login", "12345"]) == 2
    assert "10 цифр" in capsys.readouterr().err


def test_login_unknown_number(cli_client, capsys):
    assert cli.main(["login", "9990000000"]) == 1
    assert "нет аккаунта" in capsys.readouterr().err


def test_login_wrong_code(cli_client, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_prompt", lambda text, secret=False: "0000")
    assert cli.main(["login", PHONE]) == 1
    assert "не удался" in capsys.readouterr().err


def test_login_by_password(cli_client, store, monkeypatch):
    monkeypatch.setattr(cli, "_prompt", lambda text, secret=False: "hunter2")
    assert cli.main(["login", PHONE, "--password"]) == 0
    assert store.load()["phone"] == PHONE
    assert "hunter2" not in store.path.read_text(encoding="utf-8")


def test_status_reports_exit_code(cli_client, monkeypatch, capsys):
    assert cli.main(["status"]) == 1  # аноним
    monkeypatch.setattr(cli, "_prompt", lambda text, secret=False: SMS_CODE)
    assert cli.main(["login", PHONE]) == 0
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    assert PHONE in capsys.readouterr().out


def test_logout(cli_client, store, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_prompt", lambda text, secret=False: SMS_CODE)
    cli.main(["login", PHONE])
    assert cli.main(["logout"]) == 0
    assert not store.path.exists()
    assert "вышли" in capsys.readouterr().out


def test_prompt_refuses_without_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit, match="интерактивный терминал"):
        cli._prompt("код: ")
