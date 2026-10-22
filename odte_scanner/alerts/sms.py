"""SMS alerts for the same BUY/SELL pulses WhatsApp receives.

CallMeBot's WhatsApp bot is often full. These two backends send a normal
text message instead:

TextBelt (one key, no WhatsApp):
  SMS_TO          destination, e.g. 5551234567 or +15551234567
  TEXTBELT_KEY    from https://textbelt.com
                  The public key "textbelt" allows one free text per day.
                  A desk scan can emit several pulses, so use your own key.

Twilio SMS (separate from Twilio WhatsApp):
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_SMS_FROM   a Twilio number you own, E.164, e.g. +15551234567
  SMS_TO

Optional:
  SMS_ALERTS_ENABLED=0   pause SMS only
  TEXTBELT_SENDER        regulatory sender name (default Signal Desk)

Twilio is used when TWILIO_SMS_FROM is set. Otherwise TextBelt is used.
Never commit keys.
"""

from __future__ import annotations

import logging
import os
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


def configured() -> dict[str, Any]:
    """Return which SMS backend is ready (no network)."""
    to_set = bool(_e164(_env("SMS_TO", "TEXT_TO")))
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
    }


def send_sms_text(body: str, *, timeout: float = 20.0) -> dict[str, Any]:
    """Send one SMS. Prefers Twilio when TWILIO_SMS_FROM is set, else TextBelt."""
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
    if cfg["twilio"]:
        return _send_twilio(text, timeout=timeout)
    return _send_textbelt(text, timeout=timeout)


def _send_textbelt(body: str, *, timeout: float) -> dict[str, Any]:
    phone = _e164(_env("SMS_TO", "TEXT_TO"))
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


def _send_twilio(body: str, *, timeout: float) -> dict[str, Any]:
    sid = _env("TWILIO_ACCOUNT_SID")
    token = _env("TWILIO_AUTH_TOKEN")
    from_n = _e164(_env("TWILIO_SMS_FROM", "TWILIO_FROM"))
    to_n = _e164(_env("SMS_TO", "TEXT_TO"))
    if not (sid and token and from_n and to_n):
        return {"ok": False, "configured": False, "skipped": True, "error": "twilio sms env incomplete"}

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    try:
        response = requests.post(
            url,
            data={"From": from_n, "To": to_n, "Body": body},
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
