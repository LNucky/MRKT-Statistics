"""
Выгрузка ленты MRKT (POST /api/v1/feed) за последние HOURS_BACK часов (UTC) в feed.json.
Тело запроса как в твоём curl: collectionNames=[], курсорная пагинация, count=20.
Токен: MRKT_ACCESS_TOKEN или автоматически через TELEGRAM_API_ID + TELEGRAM_API_HASH (Pyrogram),
см. mrkt_auth.py. Каталог вывода: OUTPUT_DIR (в образе по умолчанию /data).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# Токен: переменная окружения MRKT_ACCESS_TOKEN (как в Authorization / cookie access_token)
TOKEN_ENV_VAR = "MRKT_ACCESS_TOKEN"

API_URL = "https://api.tgmrkt.io/api/v1/feed"
COUNT = 20
HOURS_BACK = 24  # например 48 = последние 2 суток; окно: now_utc − timedelta(hours=HOURS_BACK)
REQUEST_DELAY_S = 0.5  # пауза между успешными запросами страниц
REQUEST_TIMEOUT_S = 120  # таймаут одного HTTP-запроса (connect + read)
POST_MAX_RETRIES = 6  # повторы при сетевых сбоях и при HTTP ≠ 2xx
POST_RETRY_DELAY_S = 3.0  # базовая пауза перед повтором (экспоненциально растёт)
_SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(_SCRIPT_DIR / ".env")


def output_feed_path() -> Path:
    """Каталог выгрузки: env OUTPUT_DIR или каталог со скриптом (по умолчанию)."""
    base = Path(os.environ.get("OUTPUT_DIR", str(_SCRIPT_DIR)))
    return base / "feed.json"


def _env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "y")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


# В Docker при длинной выгрузке сотни тысяч строк «listing» в лог сильно тормозят вывод; отключи: MRKT_LOG_LISTINGS=0
LOG_LISTINGS = _env_flag("MRKT_LOG_LISTINGS", True)
# Каждые N страниц писать feed.json на диск с meta.resume_cursor (0 = только при ошибке и в конце)
CHECKPOINT_EVERY_PAGES = _env_int("MRKT_CHECKPOINT_PAGES", 100)
# При режиме TELEGRAM_*: обновлять MRKT токен каждые N страниц (0 = только при старте)
AUTH_REFRESH_PAGES = _env_int("MRKT_AUTH_REFRESH_PAGES", 1000)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://cdn.tgmrkt.io",
    "Referer": "https://cdn.tgmrkt.io/",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def payload(cursor: str) -> dict:
    return {
        "count": COUNT,
        "cursor": cursor,
        "collectionNames": [],
        "modelNames": [],
        "backdropNames": [],
        "number": None,
        "type": [],
        "minPrice": None,
        "maxPrice": None,
        "ordering": "Latest",
        "lowToHigh": False,
        "query": None,
    }


def parse_item_time(item: dict) -> datetime | None:
    for key in ("createdAt", "date", "timestamp", "finishedAt"):
        raw = item.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        s = str(raw).replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            continue
    return None


def list_from_body(body: dict) -> list:
    if isinstance(body.get("items"), list):
        return body["items"]
    if isinstance(body.get("orders"), list):
        return body["orders"]
    return []


def _is_listing_row(row: dict) -> bool:
    t = row.get("type")
    if t is None:
        return False
    return str(t).lower() == "listing"


def post_feed(session: requests.Session, body_json: dict) -> requests.Response:
    """POST feed: повторы при таймаутах/обрывах TLS и при ответах API не 2xx."""
    last: requests.Response | None = None
    last_net_err: BaseException | None = None

    for attempt in range(POST_MAX_RETRIES):
        try:
            last = session.post(API_URL, json=body_json, timeout=REQUEST_TIMEOUT_S)
        except requests.RequestException as e:
            last_net_err = e
            log(f"  сеть/tls: {type(e).__name__}: {e} (попытка {attempt + 1}/{POST_MAX_RETRIES})")
            if attempt < POST_MAX_RETRIES - 1:
                delay = POST_RETRY_DELAY_S * (2**attempt)
                log(f"  пауза {delay:.1f}s и повтор…")
                time.sleep(delay)
            continue

        last_net_err = None
        if last.ok:
            return last
        snippet = (last.text or "")[:2000]
        log(
            f"  HTTP {last.status_code} (попытка {attempt + 1}/{POST_MAX_RETRIES}), тело: {snippet!r}"
        )
        if attempt < POST_MAX_RETRIES - 1:
            delay = POST_RETRY_DELAY_S * (2**attempt)
            log(f"  пауза {delay:.1f}s и повтор…")
            time.sleep(delay)

    if last_net_err is not None:
        raise last_net_err
    assert last is not None
    last.raise_for_status()
    return last


def save_snapshot(
    collected: list[dict],
    cutoff: datetime,
    output_file: Path,
    *,
    partial: bool,
    error: str | None = None,
    resume_cursor: str | None = None,
    checkpoint: bool = False,
    checkpoint_page: int | None = None,
) -> None:
    meta: dict = {
        "api": API_URL,
        "cutoff_utc": cutoff.isoformat(),
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "hours_back": HOURS_BACK,
        "row_count": len(collected),
        "partial": partial,
        "error": error,
    }
    if resume_cursor:
        meta["resume_cursor"] = resume_cursor
    if checkpoint:
        meta["checkpoint"] = True
        meta["in_progress"] = True
    if checkpoint_page is not None:
        meta["checkpoint_page"] = checkpoint_page
    out = {"meta": meta, "items": collected}
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_cutoff_from_meta(cutoff_s: str) -> datetime:
    return datetime.fromisoformat(cutoff_s.replace("Z", "+00:00"))


def _data_dir() -> Path:
    return Path(os.environ.get("OUTPUT_DIR", str(_SCRIPT_DIR)))


def _auto_auth_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_API_ID", "").strip() and os.environ.get("TELEGRAM_API_HASH", "").strip())


def obtain_access_token(*, scheduled: bool = False) -> str:
    """MRKT токен: Pyrogram + /api/v1/auth или MRKT_ACCESS_TOKEN из env."""
    raw_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if raw_id and api_hash:
        try:
            api_id = int(raw_id)
        except ValueError:
            raise SystemExit("TELEGRAM_API_ID должен быть целым числом.") from None
        from mrkt_auth import fetch_mrkt_access_token_sync

        log(
            "Обновление MRKT токена (планово)…"
            if scheduled
            else "Получение MRKT токена через Telegram (Pyrogram)…"
        )
        try:
            return fetch_mrkt_access_token_sync(api_id, api_hash, _data_dir())
        except Exception as e:
            raise SystemExit(f"Не удалось получить токен MRKT: {e}") from e
    token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if not token:
        raise SystemExit(
            f"Задай {TOKEN_ENV_VAR} или TELEGRAM_API_ID и TELEGRAM_API_HASH в .env "
            f"(скопируй .env.example → .env)."
        )
    return token


def main() -> None:
    output_file = output_feed_path()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    token = obtain_access_token(scheduled=False)
    auto_auth = _auto_auth_configured()

    resume = _env_flag("MRKT_RESUME", False)
    now = datetime.now(timezone.utc)
    collected: list[dict] = []
    cursor: str | None = ""
    page = 0

    if resume and output_file.exists():
        try:
            raw = json.loads(output_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise SystemExit(f"Не удалось прочитать {output_file} для MRKT_RESUME: {e}") from e
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        rs = meta.get("resume_cursor")
        if not rs:
            raise SystemExit(
                f"{output_file.name}: нет meta.resume_cursor — продолжать нечего. "
                "Убери MRKT_RESUME или начни новую выгрузку."
            )
        items = raw.get("items")
        if not isinstance(items, list):
            raise SystemExit("В дампе нет массива items — файл повреждён.")
        collected = list(items)
        cursor = str(rs)
        page = int(meta.get("checkpoint_page", 0))
        cu = meta.get("cutoff_utc")
        if not cu:
            raise SystemExit("В meta нет cutoff_utc — старый дамп без поддержки resume.")
        cutoff = _parse_cutoff_from_meta(str(cu))
        log(
            f"Продолжение MRKT_RESUME: {len(collected)} записей на диске, следующая стр. с номера {page + 1}, "
            f"cutoff из дампа {cutoff.isoformat()}, файл {output_file}"
        )
    else:
        if resume:
            log("MRKT_RESUME=1, но файл дампа не найден — начинаем с нуля.")
        cutoff = now - timedelta(hours=HOURS_BACK)
        log(f"Старт: окно с {cutoff.isoformat()} по {now.isoformat()} (UTC), HOURS_BACK={HOURS_BACK}")

    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["Authorization"] = token
    session.headers["Cookie"] = f"access_token={token}"

    while True:
        page += 1
        cur_short = (cursor or "")[:12] + "…" if cursor and len(cursor) > 12 else (cursor or "∅")
        log(f"[стр. {page}] запрос feed, cursor={cur_short}")

        t_req = time.perf_counter()
        body_json = payload(cursor or "")
        try:
            r = post_feed(session, body_json)
        except requests.RequestException as e:
            err = f"{type(e).__name__}: {e}"
            if isinstance(e, requests.HTTPError) and e.response is not None:
                err = f"HTTP {e.response.status_code} {e}"
            log(f"Ошибка запроса (все повторы исчерпаны): {err}")
            save_snapshot(
                collected,
                cutoff,
                output_file,
                partial=True,
                error=err,
                resume_cursor=str(cursor or ""),
            )
            log(f"Сохранён частичный дамп ({len(collected)} записей), resume_cursor в meta → {output_file}")
            raise SystemExit(1) from e
        elapsed = time.perf_counter() - t_req
        data = r.json()
        rows = list_from_body(data)
        next_cursor = data.get("cursor")

        times = [parse_item_time(x) for x in rows]
        times_ok = [x for x in times if x is not None]
        oldest = min(times_ok) if times_ok else None
        newest = max(times_ok) if times_ok else None

        log(
            f"[стр. {page}] ответ за {elapsed:.2f}s: записей={len(rows)}, "
            f"next_cursor={'есть' if next_cursor else 'нет'}, "
            f"временной диапазон страницы: {newest.isoformat() if newest else '?'} … {oldest.isoformat() if oldest else '?'}"
        )

        listed_in_page = 0
        kept_this_page = 0
        for row in rows:
            if _is_listing_row(row):
                lt = parse_item_time(row)
                if lt is not None:
                    listed_in_page += 1
                    if LOG_LISTINGS:
                        gift = row.get("gift") or {}
                        title = gift.get("title") or gift.get("name") or row.get("collectionName") or ""
                        log(f"  listing  {lt.isoformat()}  {title!s}")

            t = parse_item_time(row)
            if t is None:
                continue
            if t >= cutoff:
                collected.append(row)
                kept_this_page += 1
        log(f"[стр. {page}] в окне (≥ cutoff): +{kept_this_page} записей, всего накоплено: {len(collected)}, listings на странице: {listed_in_page}")

        if not rows:
            log(f"[стр. {page}] пустой ответ — стоп.")
            break
        if next_cursor in (None, ""):
            log(f"[стр. {page}] cursor пуст — конец ленты.")
            break
        if len(rows) < COUNT:
            log(f"[стр. {page}] меньше {COUNT} записей — последняя страница.")
            break
        if oldest is not None and oldest < cutoff:
            log(f"[стр. {page}] самая старая запись {oldest.isoformat()} < cutoff — дальше только старее, стоп.")
            break

        if CHECKPOINT_EVERY_PAGES > 0 and page % CHECKPOINT_EVERY_PAGES == 0:
            save_snapshot(
                collected,
                cutoff,
                output_file,
                partial=False,
                error=None,
                resume_cursor=str(next_cursor),
                checkpoint=True,
                checkpoint_page=page,
            )
            log(
                f"Чекпоинт: стр.{page}, {len(collected)} записей, meta.resume_cursor обновлён → {output_file.name} "
                f"(интервал MRKT_CHECKPOINT_PAGES={CHECKPOINT_EVERY_PAGES})"
            )

        if auto_auth and AUTH_REFRESH_PAGES > 0 and page % AUTH_REFRESH_PAGES == 0:
            token = obtain_access_token(scheduled=True)
            session.headers["Authorization"] = token
            session.headers["Cookie"] = f"access_token={token}"
            log(
                f"[стр. {page}] MRKT токен обновлён (MRKT_AUTH_REFRESH_PAGES={AUTH_REFRESH_PAGES})"
            )

        cursor = next_cursor
        time.sleep(REQUEST_DELAY_S)

    save_snapshot(collected, cutoff, output_file, partial=False, error=None)
    log(f"Готово: {len(collected)} записей → {output_file}")


if __name__ == "__main__":
    main()
