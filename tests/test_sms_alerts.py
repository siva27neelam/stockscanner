"""Tests for SMS alert dispatch (no live network)."""

from __future__ import annotations

from odte_scanner.alerts import dispatcher as disp
from odte_scanner.alerts import sms


def _clear_sms(monkeypatch):
    for key in (
        "SMS_TO",
        "TEXT_TO",
        "TEXTBELT_KEY",
        "TEXTBELT_API_KEY",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_SMS_FROM",
        "TWILIO_FROM",
        "SMS_ALERTS_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)


def test_sms_not_configured(monkeypatch):
    _clear_sms(monkeypatch)
    assert sms.configured()["ok"] is False
    res = sms.send_sms_text("BUY AAPL")
    assert res["skipped"] is True


def test_e164_adds_us_country_code():
    assert sms._e164("5551234567") == "+15551234567"
    assert sms._e164("+447700900123") == "+447700900123"
    assert sms._e164("whatsapp:+15551234567") == "+15551234567"


def test_textbelt_send(monkeypatch):
    _clear_sms(monkeypatch)
    monkeypatch.setenv("SMS_TO", "5551234567")
    monkeypatch.setenv("TEXTBELT_KEY", "paid-key")

    class FakeResp:
        status_code = 200
        content = b'{"success":true,"textId":9,"quotaRemaining":3}'

        def json(self):
            return {"success": True, "textId": 9, "quotaRemaining": 3}

    calls = {}

    def fake_post(url, data=None, timeout=None, auth=None):
        calls["url"] = url
        calls["data"] = data
        return FakeResp()

    monkeypatch.setattr(sms.requests, "post", fake_post)
    res = sms.send_sms_text("BUY TSLA")
    assert res["ok"] is True
    assert res["provider"] == "textbelt"
    assert calls["url"] == "https://textbelt.com/text"
    assert calls["data"]["phone"] == "+15551234567"
    assert calls["data"]["key"] == "paid-key"
    assert "BUY TSLA" in calls["data"]["message"]
    assert "Reply STOP" in calls["data"]["message"]


def test_twilio_sms_send(monkeypatch):
    _clear_sms(monkeypatch)
    monkeypatch.setenv("SMS_TO", "+15557654321")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACxxx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_SMS_FROM", "+15551110000")
    monkeypatch.setenv("TEXTBELT_KEY", "should-not-be-used")

    class FakeResp:
        status_code = 201
        content = b'{"sid":"SM999","status":"queued"}'

        def json(self):
            return {"sid": "SM999", "status": "queued"}

    calls = {}

    def fake_post(url, data=None, auth=None, timeout=None):
        calls["url"] = url
        calls["data"] = data
        calls["auth"] = auth
        return FakeResp()

    monkeypatch.setattr(sms.requests, "post", fake_post)
    res = sms.send_sms_text("BUY AAPL")
    assert res["ok"] is True
    assert res["provider"] == "twilio-sms"
    assert calls["data"]["From"] == "+15551110000"
    assert calls["data"]["To"] == "+15557654321"
    assert calls["auth"] == ("ACxxx", "token")
    assert "whatsapp:" not in calls["data"]["From"]


def test_dispatch_sends_sms_without_whatsapp(tmp_path, monkeypatch):
    _clear_sms(monkeypatch)
    for key in (
        "CALLMEBOT_APIKEY",
        "CALLMEBOT_API_KEY",
        "TWILIO_WHATSAPP_FROM",
        "WHATSAPP_TO",
        "WHATSAPP_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SMS_TO", "5551234567")
    monkeypatch.setenv("TEXTBELT_KEY", "paid-key")

    sent: list[str] = []

    def fake_send(body, **kwargs):
        sent.append(body)
        return {"ok": True, "provider": "textbelt", "configured": True}

    monkeypatch.setattr(disp, "send_sms_text", fake_send)
    snap = {
        "actions": {
            "buy_now": [
                {
                    "symbol": "MSFT",
                    "action": "BUY_NOW",
                    "contract": "MSFT261005C00500000",
                    "ask": 1.2,
                    "signaled_at": "2026-10-05T16:00:00+00:00",
                }
            ]
        }
    }
    seen = tmp_path / "seen.json"
    primed = disp.dispatch_snapshot_alerts(snap, seen_path=seen)
    assert primed.get("primed") is True
    assert sent == []

    snap2 = {
        "actions": {
            "buy_now": [
                {
                    "symbol": "NVDA",
                    "action": "BUY_NOW",
                    "contract": "NVDA261005C00180000",
                    "ask": 0.8,
                    "signaled_at": "2026-10-05T17:00:00+00:00",
                }
            ]
        }
    }
    result = disp.dispatch_snapshot_alerts(snap2, seen_path=seen)
    assert result.get("sent") == 1
    assert "textbelt" in (result.get("providers") or [])
    assert len(sent) == 1
    assert "NVDA" in sent[0]
