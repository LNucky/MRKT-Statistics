#!/usr/bin/env python3
"""
Получение MRKT_ACCESS_TOKEN: Pyrogram открывает Mini App бота @mrkt,
из URL берётся tgWebAppData, затем POST https://api.tgmrkt.io/api/v1/auth.

Первый запуск создаёт файл сессии Telegram (логин в консоли). Секреты — в .env.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote

import httpx
from dotenv import load_dotenv
from pyrogram import Client
from pyrogram.raw.functions.messages import RequestAppWebView
from pyrogram.raw.types import InputBotAppShortName, InputUser

_SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(_SCRIPT_DIR / ".env")

MRKT_AUTH_URL = "https://api.tgmrkt.io/api/v1/auth"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _init_data_from_webapp_url(url: str) -> str:
    try:
        tail = url.split("tgWebAppData=", 1)[1]
        return unquote(tail.split("&tgWebAppVersion", 1)[0])
    except IndexError:
        _die(f"В URL нет tgWebAppData (начало URL): {url[:240]!r}…")


async def make_mrkt_auth_request(pre_token: str) -> str:
    headers = {
        "User-Agent": os.environ.get("MRKT_AUTH_UA", DEFAULT_UA).strip() or DEFAULT_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://cdn.tgmrkt.io/",
        "Content-Type": "application/json",
        "Origin": "https://cdn.tgmrkt.io",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
    payload = {"data": pre_token, "photo": None, "appId": None}
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as ac:
        r = await ac.post(MRKT_AUTH_URL, headers=headers, json=payload)
    if not r.is_success:
        body = (r.text or "")[:1200]
        _die(f"HTTP {r.status_code} от {MRKT_AUTH_URL}: {body}")
    data = r.json()
    token = data.get("token")
    if not isinstance(token, str) or not token.strip():
        _die("В ответе /api/v1/auth нет строкового token: " + json.dumps(data, ensure_ascii=False)[:800])
    return token.strip()


async def get_mrkt_access_token(api_id: int, api_hash: str, session_name: str) -> str:
    async with Client(session_name, api_id=api_id, api_hash=api_hash) as client:
        peer = await client.resolve_peer("mrkt")
        bot = InputUser(user_id=peer.user_id, access_hash=peer.access_hash)
        bot_app = InputBotAppShortName(bot_id=bot, short_name="app")
        web_view = await client.invoke(
            RequestAppWebView(
                peer=peer,
                app=bot_app,
                platform="android",
            )
        )
        init_data = _init_data_from_webapp_url(web_view.url)
        return await make_mrkt_auth_request(init_data)


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

    session_name = (os.environ.get("TELEGRAM_SESSION_NAME") or "mrkt_auth_session").strip() or "mrkt_auth_session"

    token = asyncio.run(get_mrkt_access_token(api_id, api_hash, session_name))
    if args.print_dotenv:
        print(f"MRKT_ACCESS_TOKEN={token}")
    else:
        print(token)


if __name__ == "__main__":
    main()
