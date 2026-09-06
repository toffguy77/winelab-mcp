# Внутренний API winelab.ru (реверс веб-версии)

Публичного API у ВинЛаба нет. Сайт — SAP Hybris (Commerce Accelerator), фронт
ходит в те же эндпоинты, что использует этот сервер. Снято 2026-09-05 с
`www.winelab.ru`, сборка фронта `575523989f`.

## Общее

* База: `https://www.winelab.ru`
* Все JSON-эндпоинты отвечают на `GET` c заголовком `X-Requested-With: XMLHttpRequest`.
* Авторизация не нужна — каталог, магазины и корзина работают анонимно.
* Состояние держится в куках: `JSESSIONID`, `currentRegion`, `currentPOS`.
* Мутации (`POST`) требуют `CSRFToken`. Токен лежит **не** в `<meta>`, а в
  JSON-конфиге фронта на любой HTML-странице:
  `ACC.config = {…,"CSRFToken":"b7dbc2ed-…",…}`. Надёжнее всего он достаётся из
  формы попапа логина: `GET /login/popup-form` → `<input name="CSRFToken" value="…">`.
  Без токена — `405`.
* Антибот **Qrator**: страницы `/product/<code>`, `/cart` и прочий HTML отдают
  `401` + JS-челлендж `/__qrator/qauth.js`. JSON-эндпоинты ниже челленджем
  **не** закрыты и работают без кук.

## Каталог

| Метод | Путь | Параметры | Что отдаёт |
|---|---|---|---|
| GET | `/search/results` | `q`, `page` | выдача, 21 товар на страницу |
| GET | `/search/facets` | `text` | фасеты с количествами |
| GET | `/search/autocomplete/SearchBox` | `term` | подсказки-товары |
| GET | `/search/taps` | `term` | уточняющие запросы |

Формат `q` — хибрисовский: `<текст>:<сортировка>:<фасет>:<значение>[:<фасет>:<значение>…]`

```
/search/results?q=виски:relevance:inStock:true:countryfiltr:Шотландия&page=0
```

Ответ: `{"results": [...], "pagination": {"totalNumberOfResults": 553, "numberOfPages": 27}}`.
`pageSize` и `currentPage` в ответе мусорные (`0` / `-1`).

**Сортировка сервером игнорируется** — `price-asc`, `price-desc`, `sort=` в любом
виде дают одну и ту же выдачу. Сортировать приходится на клиенте.

Ключевые фасеты: `inStock` (забрать сегодня), `isPreorder`, `price`,
`priceTagColor` (`Жёлтый ценник` / `Фиолетовый ценник`), `brands`, `countryfiltr`,
`alcoholtype`, `Capacity`, `age`, `AlcoholContent`, `manufacture`, `isNextDay`.

### Карточка товара

Отдельного JSON по товару нет, а `/product/<code>` закрыт Qrator'ом.
Обход: `\/search/results?q=<артикул>` — выдача содержит полный объект товара
(~100 полей: `classifications`, `potentialPromotions`, `sommelier`, `ean`,
`averageTasteRating`, `bonus`, …).

## Регионы и магазины

| Метод | Путь | Параметры | Комментарий |
|---|---|---|---|
| GET | `/store-finder/getAllRegionsAndCities` | — | список регионов `{"RU-MOW": "Москва и область"}` |
| GET | `/store-finder/region` | `code=RU-SPE` | переключает регион (ставит куку), отдаёт HTML |
| GET | `/store-finder` | `q`, `page` | магазины текущего региона, 10 на страницу |
| GET | `/stores/<POS>/json` | — | карточка магазина |
| GET | `/view/POSSelectorComponentController/json` | — | текущий магазин сессии |
| GET | `/store-finder/pos-name` | `posName` | подпись «Вы находитесь в …» (url-encoded текст) |

Регион влияет на цены и остатки: Dewar's White Label 0,7 — 1399.99 ₽ / сток 7 в
Москве против 1379.99 ₽ / сток 1 в Санкт-Петербурге.

### Выбор магазина внутри региона

Штатной ручки нет: фронт зовёт `POST /store-finder/pos {storeId}`, а сервер
отвечает `405` на все варианты (POST/GET, query/body, с CSRF и без) — режется
на периметре.

Обход: магазин сессии сайт держит в куке **`currentPOS`**, и её достаточно
выставить самим — `/view/POSSelectorComponentController/json` после этого
отдаёт новый `favouritePosName`, а `stockLevel` в выдаче считается по этому
магазину. `select_store()` в клиенте пробует по очереди штатные пути и в конце
куку, после каждой попытки сверяясь с POS-селектором, и сообщает,
какой способ прижился (`method`). Если сайт когда-нибудь откроет `POST
/store-finder/pos` обратно — стратегия отработает раньше куки, менять код не
придётся.

Кука входит в `KEEP_COOKIES`, так что выбранный магазин переживает
перезапуск сервера.

## Авторизация

Спринговая, с двумя способами входа. Аккаунт нужен только для того, чтобы
корзина сервера совпадала с корзиной на сайте; каталог и магазины анонимны.

| Метод | Путь | Параметры | Комментарий |
|---|---|---|---|
| GET | `/authentication/status` | — | `200` — вошли, `401` — аноним, тело пустое |
| POST | `/login/check` | `mobileNumber` | `true`/`false` — заведён ли аккаунт на номер |
| GET | `/confirmation/sendByPhone` | `number` | отправить SMS с одноразовым кодом |
| GET | `/confirmation/getByPhone` | `number` | длина кода и таймер до повторной отправки |
| POST | `/confirmation/checkByPhone` | `number`, `code` | проверить код, не логинясь |
| POST | `/j_spring_security_check` | см. ниже | собственно вход |
| GET | `/logout` | — | выход |
| GET | `/login/ping` | — | пустая страница, фронт дёргает её для проверки Qrator |

Тело входа (`application/x-www-form-urlencoded`):

```
j_username=9991234567      # 10 цифр, без +7 и без маски
j_password=<код из SMS>    # или пароль
authType=PHONE             # PHONE — вход по SMS, PASSWORD — по паролю
isCheckout=false
CSRFToken=<токен>
```

Ответ — HTML-редирект, а не JSON: успех проверяется отдельным запросом
`/authentication/status`. Форму с готовыми полями и токеном отдают
`GET /login/popup-form` (SMS) и `GET /login/popup-form-by-password`.

Номер нормализуется до 10 цифр: `/login/check` одинаково принимает
`+79991234567`, `89991234567` и `9991234567`.

## Корзина

| Метод | Путь | Тело | Комментарий |
|---|---|---|---|
| GET | `/cart/truncated/` | — | `{"items": [{"id","count","price"}]}` |
| POST | `/store-pickup/cart/add` | `productCodePost`, `qtyPost`, `CSRFToken` | работает, `{"success": true, ...}` |
| POST | `/store-pickup/cart/updateEntry` | `productCodePost`, `qtyPost` | **не удалось** — `405`/`415` |
| POST | `/cart/update`, `/cart/clearCart` | — | **не удалось** — `405`/`415` |

Content-Type для `add`: `application/x-www-form-urlencoded;charset=UTF-8`.

Корзина привязана к сессии (`JSESSIONID`). Чтобы добавления попадали в вашу
настоящую корзину, войдите в аккаунт (см. «Авторизация»); проброс кук браузера
через `WINELAB_COOKIES` остаётся запасным вариантом.

## Прочее, что видно во фронте

`/wishlist/add`, `/wishlist/product-codes`, `/my-account/*` (бонусы, адреса,
профиль, карты), `/cart/voucher`, `/product/subscribe/popup`,
`/view/CategoryNavigationComponentController/json`,
`/view/LatestArticlesComponentController/json` — не реализованы в сервере.
Теперь, когда вход работает, эти пути доступны — хороший список задач.

## Риски

* Эндпоинты недокументированы и могут поменяться без предупреждения — при
  редизайне фронта пути и формат `q` поедут первыми.
* Qrator может ужесточить правила и начать челленджить и JSON-пути; тогда
  спасают только куки живого браузера (`WINELAB_COOKIES`).
* Частые запросы стоит держать в разумных пределах: 1 товар = 1 запрос
  поисковой выдачи, кэша на стороне сервера нет.
