"""Хранилище сессии: пути под разные ОС, атомарная запись, права доступа."""

from __future__ import annotations

import json
import sys

import pytest

from winelab_mcp.session import (
    KEEP_COOKIES,
    SessionStore,
    config_dir,
    parse_cookie_header,
    session_path,
)


def test_config_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("WINELAB_CONFIG_DIR", str(tmp_path / "custom"))
    assert config_dir() == tmp_path / "custom"


@pytest.mark.parametrize(
    "platform, env, expected_tail",
    [
        ("win32", {"APPDATA": "C:\\Users\\u\\AppData\\Roaming"}, "winelab-mcp"),
        ("darwin", {}, "Library/Application Support/winelab-mcp"),
        ("linux", {"XDG_CONFIG_HOME": "/home/u/.cfg"}, ".cfg/winelab-mcp"),
    ],
)
def test_config_dir_per_platform(platform, env, expected_tail, monkeypatch):
    monkeypatch.delenv("WINELAB_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert config_dir().as_posix().endswith(expected_tail)


def test_session_path_override(tmp_path, monkeypatch):
    monkeypatch.setenv("WINELAB_SESSION", str(tmp_path / "s.json"))
    assert session_path() == tmp_path / "s.json"


def test_parse_cookie_header():
    assert parse_cookie_header("JSESSIONID=abc; currentPOS=M735 ; junk") == {
        "JSESSIONID": "abc",
        "currentPOS": "M735",
    }


def test_roundtrip_keeps_only_useful_cookies(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    store.save(
        {"JSESSIONID": "abc", "currentPOS": "M735", "_ga": "tracking"},
        region="RU-SPE",
        phone="9991234567",
    )
    assert store.cookies() == {"JSESSIONID": "abc", "currentPOS": "M735"}
    assert store.load()["region"] == "RU-SPE"
    assert "_ga" not in json.dumps(store.load())
    assert "_ga" not in KEEP_COOKIES


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-права")
def test_file_is_private(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    store.save({"JSESSIONID": "abc"})
    assert oct(store.path.stat().st_mode)[-3:] == "600"


def test_no_temp_files_left_behind(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    store.save({"JSESSIONID": "abc"})
    store.save({"JSESSIONID": "def"})
    assert [p.name for p in tmp_path.iterdir()] == ["session.json"]


def test_corrupt_and_missing_files_are_tolerated(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    assert store.load() == {} and store.cookies() == {}
    store.path.write_text("{не json", encoding="utf-8")
    assert store.load() == {}
    store.path.write_text('{"version": 999}', encoding="utf-8")
    assert store.load() == {}


def test_clear_is_idempotent(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    store.save({"JSESSIONID": "abc"})
    store.clear()
    store.clear()
    assert not store.path.exists()


def test_unwritable_location_does_not_raise(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("это файл, а не каталог", encoding="utf-8")
    store = SessionStore(blocker / "session.json")
    store.save({"JSESSIONID": "abc"})  # молча пропускаем: сессия — кэш, не источник истины
    assert store.load() == {}
