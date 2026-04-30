"""
Выгрузка ленты MRKT (POST /api/v1/feed) за последние HOURS_BACK часов (UTC) в feed.json.
Тело запроса как в твоём curl: collectionNames=[], курсорная пагинация, count=20.
Токен: переменная окружения MRKT_ACCESS_TOKEN (файл .env или env в Docker). Каталог вывода: OUTPUT_DIR (в образе по умолчанию /data).
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


def output_feed_path() -> Path:
    """Каталог выгрузки: env OUTPUT_DIR или каталог со скриптом (по умолчанию)."""
    base = Path(os.environ.get("OUTPUT_DIR", str(_SCRIPT_DIR)))
    return base / "feed.json"


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
) -> None:
    out = {
        "meta": {
            "api": API_URL,
            "cutoff_utc": cutoff.isoformat(),
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "hours_back": HOURS_BACK,
            "row_count": len(collected),
            "partial": partial,
            "error": error,
        },
        "items": collected,
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    load_dotenv(_SCRIPT_DIR / ".env")

    output_file = output_feed_path()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if not token:
        raise SystemExit(
            f"Задай {TOKEN_ENV_VAR}: экспорт в shell или файл .env рядом со scraper.py "
            f"(скопируй .env.example → .env). Токен из Authorization / cookie access_token."
        )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=HOURS_BACK)
    log(f"Старт: окно с {cutoff.isoformat()} по {now.isoformat()} (UTC), HOURS_BACK={HOURS_BACK}")

    collected: list[dict] = []

    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["Authorization"] = token
    session.headers["Cookie"] = f"access_token={token}"

    cursor: str | None = ""
    page = 0
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
            save_snapshot(collected, cutoff, output_file, partial=True, error=err)
            log(f"Сохранён частичный дамп ({len(collected)} записей) → {output_file}")
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
            log("[стр. {page}] пустой ответ — стоп.")
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

        cursor = next_cursor
        time.sleep(REQUEST_DELAY_S)

    save_snapshot(collected, cutoff, output_file, partial=False, error=None)
    log(f"Готово: {len(collected)} записей → {output_file}")


if __name__ == "__main__":
    main()
