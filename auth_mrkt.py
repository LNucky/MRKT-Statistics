#!/usr/bin/env python3
"""
CLI: получить MRKT_ACCESS_TOKEN (см. также mrkt_auth.py — ту же логику использует scraper.py).

Первый запуск с файловой сессией может запросить телефон в терминале.
Без интерактива: TELEGRAM_SESSION_STRING или заранее скопированный *.session в OUTPUT_DIR.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(_SCRIPT_DIR / ".env")


def _die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def main() -> None:
    p = argparse.ArgumentParser(description="MRKT токен через Telegram (Pyrogram + /api/v1/auth)")
    p.add_argument(
        "--print-dotenv",
        action="store_true",
        help="Вывести одну строку MRKT_ACCESS_TOKEN=… для вставки в .env",
    )
    args = p.parse_args()

    raw_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if not raw_id or not api_hash:
        _die("В .env нужны TELEGRAM_API_ID и TELEGRAM_API_HASH (https://my.telegram.org/apps).")
    try:
        api_id = int(raw_id)
    except ValueError:
        _die("TELEGRAM_API_ID должен быть целым числом.")

    data_dir = Path(os.environ.get("OUTPUT_DIR", str(_SCRIPT_DIR)))

    from mrkt_auth import fetch_mrkt_access_token_sync

    try:
        token = fetch_mrkt_access_token_sync(api_id, api_hash, data_dir)
    except Exception as e:
        _die(f"Ошибка получения токена: {e}")

    if args.print_dotenv:
        print(f"MRKT_ACCESS_TOKEN={token}")
    else:
        print(token)


if __name__ == "__main__":
    main()
