"""SMS alerts for the same BUY/SELL pulses WhatsApp receives.

CallMeBot's WhatsApp bot is often full. These two backends send a normal
text message instead:

TextBelt (one key, no WhatsApp):
  SMS_TO          one or more mobiles, comma-separated
                  e.g. 5551234567,5557654321
  TEXTBELT_KEY    from https://textbelt.com
                  The public key "textbelt" allows one free text per day.
                  Each recipient uses one credit, so use your own key.

Twilio SMS (separate from Twilio WhatsApp):
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_SMS_FROM   a Twilio number you own, E.164, e.g. +15551234567
  SMS_TO            same comma-separated list

Extra numbers can also be SMS_TO_2, SMS_TO_3, and so on, up to SMS_TO_9.

Optional:
  SMS_ALERTS_ENABLED=0   pause SMS only
  TEXTBELT_SENDER        regulatory sender name (default Signal Desk)

Twilio is used when TWILIO_SMS_FROM is set. Otherwise TextBelt is used.
Never commit keys.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

_SMS_LIMIT = 480


def _env(*names: str) -> str | None:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def _e164(raw: str | None) -> str | None:
    """Return +E.164. A 10-digit US/Canada number gets +1."""
    if not raw:
        return None
    text = raw.strip()
    if text.lower().startswith("whatsapp:"):
        text = text.split(":", 1)[1]
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 10:
        return f"+1{digits}"
    return f"+{digits}"


def _recipients() -> list[str]:
    """Every destination in SMS_TO / TEXT_TO, plus SMS_TO_2 … SMS_TO_9.

    Commas, semicolons, or newlines separate numbers in one variable.
    Space-separated values count as separate numbers only when each token
    already contains a full phone number, so "+1 555 123 4567" stays one
    recipient.
    """
    chunks: list[str] = []
    for name in ("SMS_TO", "TEXT_TO"):
        value = os.environ.get(name)
        if value and value.strip():
            chunks.append(value)
    for index in range(2, 10):
        for prefix in ("SMS_TO_", "TEXT_TO_"):
            value = os.environ.get(f"{prefix}{index}")
            if value and value.strip():
                chunks.append(value)

    found: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for piece in re.split(r"[,;\n]+", chunk):
            piece = piece.strip()
            if not piece:
                continue
            tokens = piece.split()
            if len(tokens) > 1 and all(sum(ch.isdigit() for ch in token) >= 10 for token in tokens):
                parts = tokens
            else:
                parts = [piece]
            for part in parts:
                number = _e164(part)
                if number and number not in seen:
                    seen.add(number)
                    found.append(number)
    return found


def configured() -> dict[str, Any]:
    """Return which SMS backend is ready (no network)."""
    recipients = _recipients()
    to_set = bool(recipients)
    twilio = bool(
        _env("TWILIO_ACCOUNT_SID")
        and _env("TWILIO_AUTH_TOKEN")
        and _e164(_env("TWILIO_SMS_FROM", "TWILIO_FROM"))
        and to_set
    )
    textbelt = bool(_env("TEXTBELT_KEY", "TEXTBELT_API_KEY") and to_set)
    return {
        "ok": twilio or textbelt,
        "twilio": twilio,
        "textbelt": textbelt and not twilio,
        "to_set": to_set,
        "recipients": recipients,
    }


def send_sms_text(body: str, *, timeout: float = 20.0) -> dict[str, Any]:
    """Send one SMS to every configured number.

    Prefers Twilio when TWILIO_SMS_FROM is set, else TextBelt. A send counts
    as success when at least one recipient accepts it.
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
            "error": "SMS not configured (set SMS_TO plus TEXTBELT_KEY or TWILIO_SMS_FROM)",
        }

    if "Reply STOP" not in text:
        text = f"{text}\nReply STOP to opt out"
    text = text[:_SMS_LIMIT]
    recipients = list(cfg.get("recipients") or [])
    if cfg["twilio"]:
        return _send_each(recipients, lambda phone: _send_twilio(phone, text, timeout=timeout))
    return _send_each(recipients, lambda phone: _send_textbelt(phone, text, timeout=timeout))


def _send_each(recipients: list[str], send_one) -> dict[str, Any]:
    sent: list[str] = []
    errors: list[str] = []
    last: dict[str, Any] = {}
    for phone in recipients:
        result = send_one(phone)
        last = result
        if result.get("ok"):
            sent.append(phone)
        else:
            errors.append(f"{phone}: {result.get('error') or result}")
            logger.warning("sms to %s failed: %s", phone, result.get("error"))
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
        "provider": last.get("provider") or "sms",
        "recipients": sent,
        "failed": errors,
        "status_code": last.get("status_code"),
        "text_id": last.get("text_id"),
        "sid": last.get("sid"),
        "quota_remaining": last.get("quota_remaining"),
    }


def _send_textbelt(phone: str, body: str, *, timeout: float) -> dict[str, Any]:
    key = _env("TEXTBELT_KEY", "TEXTBELT_API_KEY")
    if not (phone and key):
        return {"ok": False, "configured": False, "skipped": True, "error": "textbelt env incomplete"}
    sender = _env("TEXTBELT_SENDER") or "Signal Desk"
    try:
        response = requests.post(
            "https://textbelt.com/text",
            data={"phone": phone, "message": body, "key": key, "sender": sender},
            timeout=timeout,
        )
        payload = response.json() if response.content else {}
        if response.status_code >= 400 or not payload.get("success"):
            err = payload.get("error") or response.text[:400]
            logger.warning("textbelt failed %s: %s", response.status_code, err)
            return {
                "ok": False,
                "configured": True,
                "provider": "textbelt",
                "status_code": response.status_code,
                "quota_remaining": payload.get("quotaRemaining"),
                "error": str(err)[:400],
            }
        return {
            "ok": True,
            "configured": True,
            "provider": "textbelt",
            "text_id": payload.get("textId"),
            "quota_remaining": payload.get("quotaRemaining"),
            "status_code": response.status_code,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("textbelt exception: %s", exc)
        return {"ok": False, "configured": True, "provider": "textbelt", "error": str(exc)}


def _send_twilio(phone: str, body: str, *, timeout: float) -> dict[str, Any]:
    sid = _env("TWILIO_ACCOUNT_SID")
    token = _env("TWILIO_AUTH_TOKEN")
    from_n = _e164(_env("TWILIO_SMS_FROM", "TWILIO_FROM"))
    if not (sid and token and from_n and phone):
        return {"ok": False, "configured": False, "skipped": True, "error": "twilio sms env incomplete"}

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    try:
        response = requests.post(
            url,
            data={"From": from_n, "To": phone, "Body": body},
            auth=(sid, token),
            timeout=timeout,
        )
        if response.status_code >= 400:
            logger.warning("twilio sms failed %s: %s", response.status_code, response.text[:300])
            return {
                "ok": False,
                "configured": True,
                "provider": "twilio-sms",
                "status_code": response.status_code,
                "error": response.text[:400],
            }
        payload = response.json() if response.content else {}
        return {
            "ok": True,
            "configured": True,
            "provider": "twilio-sms",
            "sid": payload.get("sid"),
            "status": payload.get("status"),
            "status_code": response.status_code,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("twilio sms exception: %s", exc)
        return {"ok": False, "configured": True, "provider": "twilio-sms", "error": str(exc)}
