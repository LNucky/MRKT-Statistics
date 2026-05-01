# MRKT feed — scraper & analytics

Скрипты для выгрузки [ленты активности](https://cdn.tgmrkt.io) маркетплейса подарков MRKT (Telegram) через `POST /api/v1/feed` и базовой аналитики по дампу (`feed.json`).

Проект **не** аффилирован с MRKT/Telegram; используйте на свой риск и в рамках их правил.

## Возможности

- **scraper.py** — курсорная пагинация, фильтр по окну **последних N часов (UTC)**, ретраи при сетевых сбоях и не-2xx ответах, опциональный вывод в каталог `OUTPUT_DIR`.
- **parser.py** — сводка по типам событий, объёму продаж в TON, топам коллекций, таймлайны, гистограммы; эвристика «сработавший ордер» (пара `listing` → `sale` одного подарка в течение ≤1 с).
- **auth_mrkt.py** (опционально) — получить `MRKT_ACCESS_TOKEN` через Telegram: Pyrogram + Mini App бота `@mrkt` + `POST /api/v1/auth`. Зависимости в `requirements-auth.txt` (в **Docker-образ scraper не входят** — запускай на машине, где есть `.env` и логин в Telegram).

## Требования

- Python **3.12+** (рекомендуется)
- Зависимости: `pip install -r requirements.txt`
- Для **auth_mrkt.py**: дополнительно `pip install -r requirements-auth.txt`
- Токен доступа MRKT: вручную из DevTools (`Authorization` / cookie `access_token` у `cdn.tgmrkt.io`) или через **auth_mrkt.py** (см. ниже).

## Токен через auth_mrkt.py

1. Зарегистрируй приложение на [my.telegram.org/apps](https://my.telegram.org/apps) и возьми **api id** и **api hash**.
2. Установи зависимости: `pip install -r requirements.txt -r requirements-auth.txt`
3. В `.env` добавь `TELEGRAM_API_ID=число` и `TELEGRAM_API_HASH=строка` (см. `.env.example`).
4. Запусти `python auth_mrkt.py --print-dotenv` в каталоге проекта. При **первом** запуске Pyrogram запросит вход в аккаунт Telegram в терминале; появится файл сессии `ИМЯ.session` (имя задаётся `TELEGRAM_SESSION_NAME`, по умолчанию `mrkt_auth_session`).
5. Скопируй выведенную строку `MRKT_ACCESS_TOKEN=…` в `.env`.

Опционально: `MRKT_AUTH_UA` — свой `User-Agent` для запроса к `/api/v1/auth`.

## Быстрый старт (локально)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# отредактируй .env: MRKT_ACCESS_TOKEN=...
python scraper.py
python parser.py
```

- Дамп по умолчанию: `feed.json` в каталоге проекта.
- Графики и `summary.txt`: каталог `analytics_out/`.

## Настройка

| Переменная / файл | Описание |
|-------------------|----------|
| `MRKT_ACCESS_TOKEN` | Обязательно. В `.env` или в окружении. |
| `OUTPUT_DIR` | Каталог, куда пишется `feed.json` (и откуда по умолчанию читает `parser.py`). Без переменной — каталог со скриптом. |
| `MRKT_LOG_LISTINGS` | `1` / `0` — печать каждой строки `listing` в консоль. При длинной выгрузке в Docker лучше **`0`**, иначе миллионы строк сильно тормозят вывод логов (compose/docker). В `docker-compose` для scraper по умолчанию **`0`**. |
| `MRKT_CHECKPOINT_PAGES` | Каждые **N** страниц сохранять дамп и `meta.resume_cursor` (по умолчанию **100**; **`0`** — только при ошибке и в конце). |
| `MRKT_RESUME` | **`1` один раз** после обрыва: продолжить с того же `feed.json` (нужны `meta.resume_cursor` и `meta.cutoff_utc`). После успешного окончания убери из `.env`, иначе следующий запуск снова пойдёт в resume. |
| В **`auth_mrkt.py`**: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | Обязательно для скрипта получения токена; из [my.telegram.org/apps](https://my.telegram.org/apps). |
| `TELEGRAM_SESSION_NAME` | Базовое имя файла сессии Pyrogram (по умолчанию `mrkt_auth_session`). |
| В **`scraper.py`**: `HOURS_BACK` | Окно выгрузки в часах (UTC). Например `48` ≈ последние двое суток. |

Пример `.env` см. в `.env.example`.

## Parser

```bash
python parser.py --feed ./feed.json --out ./analytics_out
python parser.py --pdf   # дополнительно объединить графики в PDF (если поддерживается сборкой)
```

Пути по умолчанию завязаны на `OUTPUT_DIR` так же, как у скрапера.

## Docker

```bash
mkdir -p data
cp .env.example .env
# если твой uid на хосте не 1000 — добавь в .env для подстановки при build:
# APP_UID=1001
# APP_GID=1001
docker compose build
docker compose up scraper
```

Файлы данных на хосте: **`./data`** (`feed.json` внутри контейнера в `/data`). Пользователь в контейнере по умолчанию **uid 1000**; каталог `./data` должен быть ему доступен на запись (см. «Частые проблемы» про `Permission denied`).

Аналитика после появления дампа:

```bash
docker compose --profile analytics run --rm parser
```

Результат графиков: `./data/analytics_out/`. Для `scraper` в compose заданы DNS и `MRKT_LOG_LISTINGS=0` (см. «Частые проблемы»).

Образ также можно собрать и запускать вручную:

```bash
docker build --build-arg APP_UID="$(id -u)" --build-arg APP_GID="$(id -g)" -t mrkt-hz .
docker run --rm --user "$(id -u):$(id -g)" --env-file .env -e OUTPUT_DIR=/data -v "$(pwd)/data:/data" mrkt-hz
```

(Если не передаёшь `--user`, образ по умолчанию использует uid из `APP_UID` при сборке.)

## Структура репозитория

| Файл | Назначение |
|------|------------|
| `scraper.py` | Выгрузка ленты |
| `parser.py` | Аналитика и визуализация |
| `requirements.txt` | Зависимости Python |
| `Dockerfile`, `docker-compose.yml` | Контейнеризация |
| `.env.example` | Шаблон переменных окружения |

`.gitignore` исключает `.env`, `feed.json`, `data/`, `analytics_out/` и виртуальное окружение — не коммить секреты и большие дампы.

В **docker-compose** для `scraper`: том `./data:/data`, публичные **DNS** (8.8.8.8 и др.), **`MRKT_LOG_LISTINGS=0`** — чтобы лог не забивался миллионами строк.

## Частые проблемы

- **`Permission denied: '/data/feed.json'`** — том **`./data`** с хоста перекрывает `/data` в образе: пишет не root, а пользователь **`app`** (по умолчанию **uid 1000**). На хосте: `sudo chown -R 1000:1000 ./data` или создай `data` заранее под тем же uid. Если у тебя другой uid (например `1001`), в **`.env`** для Compose укажи `APP_UID=1001` и `APP_GID=1001`, затем **`docker compose build`** (аргументы сборки подставляются из `.env`) и снова `up`. После сбоя на первом чекпоинте проверь `./data/feed.json`: если файла нет или JSON битый — начни выгрузку заново (или с `MRKT_RESUME=1`, только если в `meta` уже был валидный дамп с `resume_cursor`).
- **Между строками `listing` в логе проходят десятки секунд** — это не медленный парсинг: Docker съедает огромный поток stdout. Отключи подробный вывод: `MRKT_LOG_LISTINGS=0` (в compose уже так для `scraper`).
- **`Temporary failure in name resolution` / не резолвится `api.tgmrkt.io`** — сбой DNS или сети у контейнера/хоста. В compose добавлены DNS Google/Cloudflare; проверь интернет и VPN. После исчерпания ретраев скрапер сохранит **частичный** `feed.json` с **`meta.resume_cursor`** (можно продолжить: `MRKT_RESUME=1`, см. таблицу настроек).
- **Обрыв после многих часов** — не обязательно всё зря: на той машине открой каталог с дампом (`./data` в Docker). Если есть `feed.json`, посмотри `meta.row_count` и **`resume_cursor`**. С актуальным скраперами при сетевой ошибке cursor пишется в дамп; **`MRKT_RESUME=1`** один раз — докачка с того же окна (`cutoff` берётся из дампа). Чекпоинты на диск: **`MRKT_CHECKPOINT_PAGES`** (по умолчанию 100). Если процесс убили «жёстко» или OOM и с последнего чекпоинта прошло много страниц — часть данных только в памяти и не сохранилась.

## Ограничения

- API может ограничивать глубину пагинации или отвечать ошибками при длинных прогонах; при сбое после ретраев сохраняется дамп с **`meta.partial`** и **`resume_cursor`** (если известен cursor упавшего запроса).
- Долгие окна (`HOURS_BACK`) дают много страниц и времени работы; между запросами есть пауза (`REQUEST_DELAY_S` в коде).
