"""Telegram Bot API alerts for BUY/SELL pulses.

Free. Each person must open the bot and press Start once. A bot cannot
text a phone number that has never talked to it.

Env:
  TELEGRAM_BOT_TOKEN   from @BotFather
  TELEGRAM_CHAT_ID     one or more chat ids, comma-separated
                       e.g. 123456789,-1001234567890
                       A negative id is a group, and everyone in that group
                       sees the message.

Extra people can also be TELEGRAM_CHAT_ID_2 … TELEGRAM_CHAT_ID_9.

Optional:
  TELEGRAM_ALERTS_ENABLED=0 to pause Telegram only

Never commit the token.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

_CHAT_TOKEN = re.compile(r"@?[A-Za-z][A-Za-z0-9_]{3,}|-?\d+")


def _env(*names: str) -> str | None:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def _chat_ids() -> list[str]:
    """Every chat in TELEGRAM_CHAT_ID, plus TELEGRAM_CHAT_ID_2 … _9."""
    chunks: list[str] = []
    for name in ("TELEGRAM_CHAT_ID", "TELEGRAM_CHAT"):
        value = os.environ.get(name)
        if value and value.strip():
            chunks.append(value)
    for index in range(2, 10):
        value = os.environ.get(f"TELEGRAM_CHAT_ID_{index}")
        if value and value.strip():
            chunks.append(value)

    found: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for token in _CHAT_TOKEN.findall(chunk):
            if token not in seen:
                seen.add(token)
                found.append(token)
    return found


def configured() -> dict[str, Any]:
    token = _env("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN")
    chats = _chat_ids()
    enabled_raw = (os.environ.get("TELEGRAM_ALERTS_ENABLED") or "").strip().lower()
    enabled = enabled_raw not in {"0", "false", "no", "off"}
    return {
        "ok": bool(token and chats and enabled),
        "token_set": bool(token),
        "chat_set": bool(chats),
        "chats": chats,
        "enabled": enabled,
    }


def send_telegram_text(body: str, *, timeout: float = 20.0) -> dict[str, Any]:
    """Send one plain-text message to every configured chat.

    Success means at least one chat accepted it.
    """
    text = (body or "").strip()
    if not text:
        return {"ok": False, "skipped": True, "error": "empty body"}

    cfg = configured()
    if not cfg["ok"]:
        return {
            "ok": False,
            "configured": False,
            "skipped": True,
            "error": "Telegram not configured (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)",
        }

    token = _env("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN")
    sent: list[str] = []
    errors: list[str] = []
    last: dict[str, Any] = {}
    for chat_id in cfg["chats"]:
        last = _send_one(token or "", chat_id, text[:4000], timeout=timeout)
        if last.get("ok"):
            sent.append(chat_id)
        else:
            errors.append(f"{chat_id}: {last.get('error') or last}")
            logger.warning("telegram to %s failed: %s", chat_id, last.get("error"))
    if not sent:
        failed = dict(last)
        failed["ok"] = False
        failed["recipients"] = []
        failed["failed"] = errors
        if errors:
            failed["error"] = "; ".join(errors)[:400]
        return failed
    return {
        "ok": True,
        "configured": True,
        "provider": "telegram",
        "recipients": sent,
        "failed": errors,
        "message_id": last.get("message_id"),
        "status_code": last.get("status_code"),
    }


def _send_one(token: str, chat_id: str, text: str, *, timeout: float) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=timeout,
        )
        payload = response.json() if response.content else {}
        if response.status_code >= 400 or not payload.get("ok"):
            err = payload.get("description") or response.text[:400]
            return {
                "ok": False,
                "configured": True,
                "provider": "telegram",
                "status_code": response.status_code,
                "error": str(err)[:400],
            }
        result = payload.get("result") if isinstance(payload, dict) else {}
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return {
            "ok": True,
            "configured": True,
            "provider": "telegram",
            "message_id": message_id,
            "status_code": response.status_code,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "configured": True, "provider": "telegram", "error": str(exc)}
