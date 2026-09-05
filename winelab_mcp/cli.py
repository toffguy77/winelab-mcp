"""Командная строка: запуск сервера и вход в аккаунт вне чата.

    winelab-mcp                     # stdio (так его зовут MCP-хосты)
    winelab-mcp serve --transport http --port 8000
    winelab-mcp login               # вход по SMS-коду
    winelab-mcp status / logout
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import __version__
from .client import AuthError, WinelabClient, normalize_phone
from .session import SessionStore, session_path

TRANSPORTS = {"stdio": "stdio", "http": "streamable-http", "sse": "sse"}


def _client() -> WinelabClient:
    return WinelabClient(store=SessionStore())


def _prompt(text: str, secret: bool = False) -> str:
    """Читает ответ с терминала; в неинтерактивном режиме — понятная ошибка."""
    if not sys.stdin.isatty():
        raise SystemExit(
            "нужен интерактивный терминал: запустите `winelab-mcp login` вручную"
        )
    value = (getpass.getpass(text) if secret else input(text)).strip()
    if not value:
        raise SystemExit("пустой ввод, отмена")
    return value


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import mcp

    transport = TRANSPORTS[args.transport]
    if transport != "stdio":
        # host/port относятся только к сетевым транспортам
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        print(
            f"winelab-mcp {__version__}: {transport} на http://{args.host}:{args.port}",
            file=sys.stderr,
        )
    mcp.run(transport=transport)
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    client = _client()
    try:
        phone = args.phone or _prompt("Телефон (+7...): ")
        try:
            number = normalize_phone(phone)
        except ValueError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 2

        if args.password:
            secret = _prompt(f"Пароль для {number}: ", secret=True)
            ok = client.login_with_password(number, secret)
        else:
            if not client.phone_registered(number):
                print(
                    f"✗ на номер {number} нет аккаунта ВинЛаб — "
                    "сначала зарегистрируйтесь на winelab.ru",
                    file=sys.stderr,
                )
                return 1
            client.send_sms_code(number)
            print(f"Код отправлен на {number}.")
            ok = client.login_with_code(number, _prompt("Код из SMS: "))

        if not ok:
            print("✗ вход не удался: код/пароль не подошёл", file=sys.stderr)
            return 1
        print(f"✓ вошли как {number}\n  сессия: {session_path()}")
        return 0
    except AuthError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


def cmd_status(args: argparse.Namespace) -> int:
    client = _client()
    try:
        store = SessionStore()
        data = store.load()
        logged_in = client.is_authenticated()
        print(f"winelab-mcp {__version__}")
        print(f"сессия:  {session_path()}{'' if data else ' (нет)'}")
        print(f"регион:  {client.region}")
        print(f"аккаунт: {'вошли как ' + str(data.get('phone')) if logged_in else 'аноним'}")
        return 0 if logged_in else 1
    finally:
        client.close()


def cmd_logout(args: argparse.Namespace) -> int:
    client = _client()
    try:
        client.logout()
        print("✓ вышли, сохранённая сессия удалена")
        return 0
    finally:
        client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="winelab-mcp",
        description="MCP-сервер к каталогу ВинЛаб (winelab.ru)",
    )
    parser.add_argument("--version", action="version", version=f"winelab-mcp {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="запустить MCP-сервер (по умолчанию)")
    serve.add_argument(
        "--transport",
        choices=sorted(TRANSPORTS),
        default="stdio",
        help="stdio для локальных хостов, http — чтобы шарить сервер по сети",
    )
    serve.add_argument("--host", default="127.0.0.1", help="только для http/sse")
    serve.add_argument("--port", type=int, default=8000, help="только для http/sse")
    serve.set_defaults(func=cmd_serve)

    login = sub.add_parser("login", help="войти в аккаунт ВинЛаб (SMS-код)")
    login.add_argument("phone", nargs="?", help="номер в любом формате")
    login.add_argument(
        "--password",
        action="store_true",
        help="войти по паролю вместо SMS (пароль не сохраняется, только сессия)",
    )
    login.set_defaults(func=cmd_login)

    sub.add_parser("status", help="показать состояние сессии").set_defaults(func=cmd_status)
    sub.add_parser("logout", help="выйти и стереть сессию").set_defaults(func=cmd_logout)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # Голый вызов `winelab-mcp` — это запуск сервера: так его прописывают хосты.
    if not argv:
        argv = ["serve"]
    args = parser.parse_args(argv)
    code = args.func(args)
    return int(code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
