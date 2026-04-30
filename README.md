# MRKT feed — scraper & analytics

Скрипты для выгрузки [ленты активности](https://cdn.tgmrkt.io) маркетплейса подарков MRKT (Telegram) через `POST /api/v1/feed` и базовой аналитики по дампу (`feed.json`).

Проект **не** аффилирован с MRKT/Telegram; используйте на свой риск и в рамках их правил.

## Возможности

- **scraper.py** — курсорная пагинация, фильтр по окну **последних N часов (UTC)**, ретраи при сетевых сбоях и не-2xx ответах, опциональный вывод в каталог `OUTPUT_DIR`.
- **parser.py** — сводка по типам событий, объёму продаж в TON, топам коллекций, таймлайны, гистограммы; эвристика «сработавший ордер» (пара `listing` → `sale` одного подарка в течение ≤1 с).

## Требования

- Python **3.12+** (рекомендуется)
- Зависимости: `pip install -r requirements.txt`
- Токен доступа MRKT: тот же, что в заголовке `Authorization` или в cookie `access_token` у `cdn.tgmrkt.io` (из DevTools → Network).

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
cp .env.example .env
docker compose build
docker compose up scraper
```

Файлы данных на хосте: **`./data`** (`feed.json` внутри контейнера в `/data`).

Аналитика после появления дампа:

```bash
docker compose --profile analytics run --rm parser
```

Результат графиков: `./data/analytics_out/`.

Образ также можно собрать и запускать вручную:

```bash
docker build -t mrkt-hz .
docker run --rm --env-file .env -e OUTPUT_DIR=/data -v "$(pwd)/data:/data" mrkt-hz
```

## Структура репозитория

| Файл | Назначение |
|------|------------|
| `scraper.py` | Выгрузка ленты |
| `parser.py` | Аналитика и визуализация |
| `requirements.txt` | Зависимости Python |
| `Dockerfile`, `docker-compose.yml` | Контейнеризация |
| `.env.example` | Шаблон переменных окружения |

`.gitignore` исключает `.env`, `feed.json`, `data/`, `analytics_out/` и виртуальное окружение — не коммить секреты и большие дампы.

## Ограничения

- API может ограничивать глубину пагинации или отвечать ошибками при длинных прогонах; при сбое сохраняется **частичный** дамп (`meta.partial` в JSON).
- Долгие окна (`HOURS_BACK`) дают много страниц и времени работы; между запросами есть пауза (`REQUEST_DELAY_S` в коде).
