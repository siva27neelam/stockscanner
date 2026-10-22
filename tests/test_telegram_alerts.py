"""Tests for Telegram alert dispatch (no live network)."""

from __future__ import annotations

from odte_scanner.alerts import dispatcher as disp
from odte_scanner.alerts import telegram as tg


def _clear(monkeypatch):
    keys = [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_CHAT",
        "TELEGRAM_ALERTS_ENABLED",
    ]
    for index in range(2, 10):
        keys.append(f"TELEGRAM_CHAT_ID_{index}")
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_telegram_not_configured(monkeypatch):
    _clear(monkeypatch)
    assert tg.configured()["ok"] is False
    res = tg.send_telegram_text("hello")
    assert res["skipped"] is True


def test_chat_ids_from_list_and_extra_var(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789, -100999, 123456789")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_2", "@familydesk")
    assert tg._chat_ids() == ["123456789", "-100999", "@familydesk"]


def test_telegram_sends_each_chat(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111,222")

    class FakeResp:
        status_code = 200
        content = b'{"ok":true,"result":{"message_id":7}}'

        def json(self):
            return {"ok": True, "result": {"message_id": 7}}

    chats: list[str] = []

    def fake_post(url, json=None, timeout=None):
        chats.append(json["chat_id"])
        assert "123:ABC" in url
        assert "BUY NVDA" in json["text"]
        return FakeResp()

    monkeypatch.setattr(tg.requests, "post", fake_post)
    res = tg.send_telegram_text("BUY NVDA")
    assert res["ok"] is True
    assert res["provider"] == "telegram"
    assert res["recipients"] == ["111", "222"]
    assert chats == ["111", "222"]


def test_partial_chat_failure_still_counts(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111 222")

    def fake_post(url, json=None, timeout=None):
        class FakeResp:
            def __init__(self, ok: bool):
                self.status_code = 200 if ok else 400
                self.content = b"{}"
                self.text = "nope"
                self._ok = ok

            def json(self):
                if self._ok:
                    return {"ok": True, "result": {"message_id": 1}}
                return {"ok": False, "description": "chat not found"}

        return FakeResp(json["chat_id"] == "111")

    monkeypatch.setattr(tg.requests, "post", fake_post)
    res = tg.send_telegram_text("BUY AMD")
    assert res["ok"] is True
    assert res["recipients"] == ["111"]
    assert res["failed"]


def test_dispatch_sends_telegram_only(tmp_path, monkeypatch):
    _clear(monkeypatch)
    for key in (
        "SMS_TO",
        "TEXTBELT_KEY",
        "TWILIO_SMS_FROM",
        "CALLMEBOT_APIKEY",
        "WHATSAPP_TO",
        "WHATSAPP_TOKEN",
        "TWILIO_ACCOUNT_SID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42,43")

    sent: list[str] = []

    def fake_send(body, **kwargs):
        sent.append(body)
        return {"ok": True, "provider": "telegram", "configured": True, "recipients": ["42", "43"]}

    monkeypatch.setattr(disp, "send_telegram_text", fake_send)
    seen = tmp_path / "seen.json"
    seed = {
        "actions": {
            "buy_now": [
                {
                    "symbol": "MSFT",
                    "action": "BUY_NOW",
                    "contract": "MSFT1",
                    "signaled_at": "2026-10-05T16:00:00+00:00",
                }
            ]
        }
    }
    primed = disp.dispatch_snapshot_alerts(seed, seen_path=seen)
    assert primed.get("primed") is True
    assert sent == []

    fresh = {
        "actions": {
            "buy_now": [
                {
                    "symbol": "NVDA",
                    "action": "BUY_NOW",
                    "contract": "NVDA1",
                    "signaled_at": "2026-10-05T17:00:00+00:00",
                }
            ]
        }
    }
    result = disp.dispatch_snapshot_alerts(fresh, seen_path=seen)
    assert result.get("sent") == 1
    assert result.get("providers") == ["telegram"]
    assert len(sent) == 1
    assert "NVDA" in sent[0]
