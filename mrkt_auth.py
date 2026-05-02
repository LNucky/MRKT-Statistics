"""
MRKT access token: Pyrogram (Mini App @mrkt) + POST https://api.tgmrkt.io/api/v1/auth.

Файл сессии Telegram: {workdir}/{имя}.session, где workdir = OUTPUT_DIR (или родитель для абсолютного TELEGRAM_SESSION_NAME).
Или TELEGRAM_SESSION_STRING — без файла на диске.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote

import httpx
from pyrogram import Client
from pyrogram.raw.functions.messages import RequestAppWebView
from pyrogram.raw.types import InputBotAppShortName, InputUser

MRKT_AUTH_URL = "https://api.tgmrkt.io/api/v1/auth"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def init_data_from_webapp_url(url: str) -> str:
    try:
        tail = url.split("tgWebAppData=", 1)[1]
        return unquote(tail.split("&tgWebAppVersion", 1)[0])
    except IndexError as e:
        raise ValueError(f"В URL нет tgWebAppData (начало): {url[:240]!r}…") from e


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
        raise RuntimeError(f"HTTP {r.status_code} от {MRKT_AUTH_URL}: {body}")
    data = r.json()
    token = data.get("token")
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError(
            "В ответе /api/v1/auth нет строкового token: " + json.dumps(data, ensure_ascii=False)[:800]
        )
    return token.strip()


async def _webview_then_token(client: Client) -> str:
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
    init_data = init_data_from_webapp_url(web_view.url)
    return await make_mrkt_auth_request(init_data)


def resolve_session_paths(data_dir: Path) -> tuple[str | None, str, Path]:
    """
    Для Client(..., workdir=...):
    - при строке сессии: (string, _, workdir) — workdir для совместимости;
    - иначе: (None, имя_без_пути, каталог_где_лежит_.session).
    """
    data_dir = data_dir.expanduser().resolve()
    ss = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()
    if ss:
        return ss, "", data_dir

    raw = (os.environ.get("TELEGRAM_SESSION_NAME") or "mrkt_auth_session").strip() or "mrkt_auth_session"
    p = Path(raw)
    if p.is_absolute():
        workdir = p.parent.resolve()
        name = p.name
    else:
        workdir = data_dir
        name = raw
    return None, name, workdir


def _session_file_path(workdir_path: Path, session_name: str) -> Path:
    return workdir_path / f"{session_name}.session"


def _require_login_possible(
    session_string: str | None,
    session_name: str,
    workdir_path: Path,
) -> None:
    """В Docker stdin не TTY — Pyrogram не может запросить телефон без готовой сессии."""
    if session_string:
        return
    sf = _session_file_path(workdir_path, session_name)
    if sf.is_file() and sf.stat().st_size > 0:
        return
    if sys.stdin.isatty() and sys.stdout.isatty():
        return
    wd = workdir_path
    raise RuntimeError(
        "Нет сохранённой Telegram-сессии, а ввод телефона в Docker недоступен (нет TTY). "
        "Сделай один из вариантов:\n"
        "  • Добавь в .env TELEGRAM_SESSION_STRING=… — строку сессии Pyrogram "
        "(после входа на хосте: export_session_string() или см. доку Pyrogram).\n"
        f"  • Скопируй на хост в каталог тома (например ./data) файл «{session_name}.session», "
        "предварительно создав его на машине с клавиатурой: "
        "`python auth_mrkt.py` в venv с теми же TELEGRAM_API_ID/HASH.\n"
        f"  • Один раз интерактивно: `docker compose run --rm -it scraper` (ключи -it), "
        f"ввести телефон/код; затем обычный `up` — файл появится в {wd}."
    )


def fetch_mrkt_access_token_sync(api_id: int, api_hash: str, data_dir: Path) -> str:
    return asyncio.run(fetch_mrkt_access_token_async(api_id, api_hash, data_dir))


async def fetch_mrkt_access_token_async(api_id: int, api_hash: str, data_dir: Path) -> str:
    session_string, session_name, workdir_path = resolve_session_paths(data_dir)
    try:
        workdir_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"Не удалось создать каталог сессии Pyrogram {workdir_path}: {e}. "
            "Проверь права на OUTPUT_DIR / том ./data (chown для uid контейнера)."
        ) from e

    wd = str(workdir_path)

    if not session_string:
        _require_login_possible(session_string, session_name, workdir_path)

    try:
        if session_string:
            async with Client(
                "mrkt_inline",
                api_id=api_id,
                api_hash=api_hash,
                session_string=session_string,
                workdir=wd,
            ) as client:
                return await _webview_then_token(client)
        async with Client(
            session_name,
            api_id=api_id,
            api_hash=api_hash,
            workdir=wd,
        ) as client:
            return await _webview_then_token(client)
    except EOFError as e:
        raise RuntimeError(
            "Pyrogram ждал ввод телефона, но stdin закрыт (типично для docker compose up без TTY). "
            "Используй TELEGRAM_SESSION_STRING или положи *.session в OUTPUT_DIR, либо "
            "`docker compose run --rm -it scraper` для первого входа."
        ) from e
    except OSError as e:
        err = str(e).lower()
        if "unable to open database" in err or "readonly" in err:
            raise RuntimeError(
                f"SQLite/Pyrogram не может записать сессию в {wd} ({e}). "
                "Часто это права на том: на хосте sudo chown -R 1000:1000 ./data "
                "или APP_UID/APP_GID как у владельца ./data и docker compose build."
            ) from e
        raise

