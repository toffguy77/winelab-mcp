# winelab-mcp

[![CI](https://github.com/toffguy77/winelab-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/toffguy77/winelab-mcp/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

MCP-сервер к каталогу [winelab.ru](https://www.winelab.ru) (ВинЛаб): поиск
товаров, фасеты, карточки, магазины, регионы, корзина и вход в аккаунт.
Работает с любым MCP-хостом — Claude Code, Claude Desktop, Cursor, VS Code,
Windsurf и всем, что умеет stdio или streamable-HTTP.

> *An MCP server for the Russian wine retail chain WineLab. Docs are in Russian
> because the catalogue, the site and its users are.*

Публичного API у сети нет — сервер ходит во внутренние JSON-эндпоинты
веб-версии (SAP Hybris). Что именно и с какими ограничениями — в [API.md](API.md).

## Быстрый старт

Без установки, прямо из репозитория:

```bash
uvx --from git+https://github.com/toffguy77/winelab-mcp winelab-mcp
```

Или в своё окружение:

```bash
pip install git+https://github.com/toffguy77/winelab-mcp
winelab-mcp            # stdio-сервер; так его запускают MCP-хосты
winelab-mcp status     # что с сессией и регионом
```

Нужен Python 3.10+. Linux, macOS и Windows поддерживаются одинаково.

## Подключение к хосту

**Claude Code** — одной командой:

```bash
claude mcp add winelab -- uvx --from git+https://github.com/toffguy77/winelab-mcp winelab-mcp
```

**Claude Desktop** — `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/`,
Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "winelab": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/toffguy77/winelab-mcp", "winelab-mcp"],
      "env": { "WINELAB_REGION": "RU-MOW" }
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json`), **Windsurf** (`~/.codeium/windsurf/mcp_config.json`)
и **VS Code** (`.vscode/mcp.json`, ключ `servers` вместо `mcpServers`) — тот же
блок. Если пакет уже установлен в окружение, вместо `uvx` достаточно
`"command": "winelab-mcp", "args": []`.

**Один сервер на несколько машин** — поднимите его по HTTP:

```bash
winelab-mcp serve --transport http --host 0.0.0.0 --port 8000
# хост подключается к http://<адрес>:8000/mcp
```

Аутентификации у HTTP-транспорта нет — держите его в доверенной сети или за
обратным прокси. Сессия ВинЛаба в этом режиме общая для всех, кто подключился.

## Вход в аккаунт

Копировать куки из браузера больше не нужно. Вход — по одноразовому SMS-коду,
на диск ложится только сессионная кука; пароль не сохраняется никогда.

Из терминала (рекомендуется — код не попадает в переписку):

```bash
winelab-mcp login          # спросит телефон и код из SMS
winelab-mcp login --password   # если привычнее пароль
winelab-mcp status
winelab-mcp logout         # выход + удаление сессии с диска
```

Или прямо из диалога с моделью — инструментами `auth_send_code` → `auth_login`.

Сессия переживает перезапуск сервера и лежит в файле с правами `0600`:

| ОС | Путь |
|---|---|
| macOS | `~/Library/Application Support/winelab-mcp/session.json` |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/winelab-mcp/session.json` |
| Windows | `%APPDATA%\winelab-mcp\session.json` |

Зачем входить: корзина становится общей с сайтом — то, что модель положила
через `cart_add`, вы видите в своём аккаунте на winelab.ru. Каталог, магазины и
цены работают и анонимно.

## Переменные окружения

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `WINELAB_REGION` | `RU-MOW` | регион при старте; влияет на цены и остатки |
| `WINELAB_MAX_PAGES` | `5` | потолок страниц выдачи, сканируемых при сортировке |
| `WINELAB_CONFIG_DIR` | по правилам ОС | где хранить сессию (удобно для нескольких аккаунтов) |
| `WINELAB_SESSION` | `<config>/session.json` | полный путь к файлу сессии |
| `WINELAB_COOKIES` | — | запасной путь: куки браузера строкой `JSESSIONID=…; currentPOS=…` |
| `WINELAB_UA` | Chrome/macOS | User-Agent |
| `WINELAB_BASE` | `https://www.winelab.ru` | база |

## Инструменты

| Инструмент | Что делает |
|---|---|
| `search_products` | поиск с фильтрами (`in_stock_only`, цена, страна, бренд, тип, объём) и сортировкой |
| `product_details` | полная карточка по артикулу: атрибуты, рейтинги, акции, штрихкод |
| `suggest` | подсказки: похожие товары + уточняющие запросы |
| `list_filters` | доступные фасеты и их значения для запроса |
| `list_regions` / `set_region` | список регионов и переключение |
| `current_store` | магазин, к которому привязана сессия |
| `find_stores` / `store_details` | магазины региона по адресу, часы работы, метро |
| `cart_view` / `cart_add` | корзина |
| `auth_status` / `auth_send_code` / `auth_login` / `auth_logout` | вход по SMS-коду |

Примеры запросов, которые сервер закрывает:

* «Найди односолодовый виски Шотландия до 5000 ₽, что можно забрать сегодня»
* «Сколько стоит артикул 1019872 и в скольких магазинах он есть»
* «Ближайшие ВинЛабы на Ленинском, во сколько закрываются»
* «Переключись на Питер и сравни цену»

## Ограничения

* **Серверная сортировка не работает** — сайт её игнорирует, поэтому
  `sort=price_asc` сортирует внутри просканированных страниц
  (`pages` × 21 товар, по умолчанию 2). Для честного «самое дешёвое по всей
  выдаче» поднимайте `pages`.
* **Остатки — по региону, не по конкретному магазину.** Выбор магазина внутри
  региона сайт не отдаёт (`405`), см. API.md. Есть `stores_with_stock` — в
  скольких магазинах товар в наличии.
* **Из корзины нельзя удалять** — эндпоинты обновления и очистки отвечают
  `405`/`415`. Добавление работает, удаление — руками на сайте.
* **Антибот Qrator.** JSON-пути сейчас открыты, но если инструмент вернёт
  `error_kind: "blocked"` — откройте winelab.ru в браузере, скопируйте куки и
  положите их в `WINELAB_COOKIES`.
* Эндпоинты недокументированы и могут сломаться при редизайне сайта. За этим
  следит еженедельная канарейка в CI (`tests/test_live.py`).

## Разработка

```bash
git clone https://github.com/toffguy77/winelab-mcp
cd winelab-mcp
uv venv && uv pip install -e ".[dev]"

pytest                       # оффлайн: сеть подменена httpx.MockTransport
WINELAB_LIVE=1 pytest tests/test_live.py   # канарейка по настоящему сайту
ruff check .
```

Тесты по умолчанию не ходят в сеть и не трогают вашу сессию: файл пишется во
временный каталог. Живые тесты только читают каталог — SMS никому не уходит.

## Дисклеймер

Проект не связан с ООО «Винлаб» и не одобрен им; «ВинЛаб» — товарный знак
правообладателя. Это неофициальный клиент для личного использования: он ходит в
те же публичные эндпоинты, что и сайт в браузере, от имени самого пользователя.
Соблюдайте условия использования сайта и не устраивайте нагрузку — кэша на
стороне сервера нет, один товар это один запрос. Алкоголь продаётся только
совершеннолетним; оформление и оплата заказа происходят на сайте, а не здесь.

## Лицензия

[MIT](LICENSE)
