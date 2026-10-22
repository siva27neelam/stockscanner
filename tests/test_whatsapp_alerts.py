"""Tests for WhatsApp alert dispatch (no live network)."""

from __future__ import annotations

from odte_scanner.alerts import dispatcher as disp
from odte_scanner.alerts import whatsapp as wa


def test_whatsapp_not_configured(monkeypatch):
    for k in (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_WHATSAPP_FROM",
        "WHATSAPP_TO",
        "WHATSAPP_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = wa.configured()
    assert cfg["ok"] is False
    res = wa.send_whatsapp_text("hello")
    assert res["skipped"] is True


def test_normalize_to():
    assert wa._normalize_to("+15551234567") == "whatsapp:+15551234567"
    assert wa._normalize_to("whatsapp:+15551234567") == "whatsapp:+15551234567"
    assert wa._normalize_to("15551234567") == "whatsapp:+15551234567"


def test_collect_trade_alerts_from_snapshot():
    snap = {
        "actions": {
            "buy_now": [
                {
                    "symbol": "AAPL",
                    "action": "BUY_NOW",
                    "strike": 335,
                    "expiry": "2026-10-05",
                    "ask": 0.55,
                    "right": "C",
                    "signaled_at_cst": "Oct 5, 2026, 11:55:07 AM CDT",
                }
            ],
            "sell_now": [],
        },
        "rip_radar": {
            "buy_rip": [
                {
                    "symbol": "TSLA",
                    "action": "BUY_RIP",
                    "strike": 390,
                    "expiry": "2026-10-09",
                    "ask": 2.7,
                    "contract": "TSLA261009C00390000",
                }
            ]
        },
        "lottery": {"buy_now": [], "sell_now": []},
        "beauty_monthly": {},
        "level_watch": {},
        "challenge": {},
        "odte_1k": {},
        "ml6": {},
    }
    alerts = disp.collect_trade_alerts(snap)
    assert len(alerts) == 2
    symbols = {a["symbol"] for a in alerts}
    assert symbols == {"AAPL", "TSLA"}
    assert all("BUY" in a["side"] for a in alerts)
    assert "AAPL" in alerts[0]["message"] or "AAPL" in alerts[1]["message"]


def test_dispatch_primes_then_sends(tmp_path, monkeypatch):
    monkeypatch.delenv("CALLMEBOT_APIKEY", raising=False)
    monkeypatch.delenv("CALLMEBOT_API_KEY", raising=False)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACxxx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    monkeypatch.setenv("WHATSAPP_TO", "+15551234567")

    sent: list[str] = []

    def fake_send(body, **kwargs):
        sent.append(body)
        return {"ok": True, "provider": "twilio", "configured": True}

    monkeypatch.setattr(disp, "send_whatsapp_text", fake_send)

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
        },
        "lottery": {},
        "rip_radar": {},
        "beauty_monthly": {},
        "level_watch": {},
        "challenge": {},
        "odte_1k": {},
        "ml6": {},
    }
    seen = tmp_path / "seen.json"
    r1 = disp.dispatch_snapshot_alerts(snap, seen_path=seen)
    assert r1.get("primed") is True
    assert r1.get("sent") == 0
    assert sent == []

    # Same pulse — still no send
    r2 = disp.dispatch_snapshot_alerts(snap, seen_path=seen)
    assert r2.get("sent") == 0

    # New pulse
    snap2 = {
        **snap,
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
        },
    }
    r3 = disp.dispatch_snapshot_alerts(snap2, seen_path=seen)
    assert r3.get("sent") == 1
    assert "twilio" in (r3.get("providers") or [])
    assert len(sent) == 1
    assert "NVDA" in sent[0]


def test_callmebot_send(monkeypatch):
    monkeypatch.setenv("WHATSAPP_TO", "15551234567")
    monkeypatch.setenv("CALLMEBOT_APIKEY", "123456")
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM", "WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"):
        monkeypatch.delenv(k, raising=False)

    class FakeResp:
        status_code = 200
        text = "OK"

    calls = {}

    def fake_get(url, timeout=None):
        calls["url"] = url
        return FakeResp()

    monkeypatch.setattr(wa.requests, "get", fake_get)
    res = wa.send_whatsapp_text("BUY TSLA")
    assert res["ok"] is True
    assert res["provider"] == "callmebot"
    assert "15551234567" in calls["url"]
    assert "apikey=123456" in calls["url"]


def test_twilio_send_posts(monkeypatch):
    monkeypatch.delenv("CALLMEBOT_APIKEY", raising=False)
    monkeypatch.delenv("CALLMEBOT_API_KEY", raising=False)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACxxx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    monkeypatch.setenv("WHATSAPP_TO", "+15551234567")

    class FakeResp:
        status_code = 201
        content = b'{"sid":"SM123","status":"queued"}'

        def json(self):
            return {"sid": "SM123", "status": "queued"}

    calls = {}

    def fake_post(url, data=None, auth=None, timeout=None):
        calls["url"] = url
        calls["data"] = data
        calls["auth"] = auth
        return FakeResp()

    monkeypatch.setattr(wa.requests, "post", fake_post)
    res = wa.send_whatsapp_text("BUY AAPL")
    assert res["ok"] is True
    assert res["provider"] == "twilio"
    assert calls["data"]["To"] == "whatsapp:+15551234567"
    assert "BUY AAPL" in calls["data"]["Body"]


def test_collects_gold_setup_and_golden_flow_only():
    snap = {
        "sure_winners": {
            "winners": [
                {
                    "symbol": "TSLA",
                    "action": "SURE_WINNER",
                    "sure_winner": True,
                    "confidence_tier": "HIGH",
                    "contract": "TSLA261023C00385000",
                    "strike": 385,
                    "expiry": "2026-10-23",
                    "ask": 7.6,
                    "signaled_at": "2026-10-20T20:54:37+00:00",
                },
                {
                    "symbol": "F",
                    "action": "SURE_WINNER",
                    "sure_winner": True,
                    "confidence_tier": "SOLID",
                    "contract": "F261023C00012000",
                    "ask": 0.4,
                },
            ]
        },
        "actions": {
            "buy_now": [
                {
                    "symbol": "JNJ",
                    "action": "SURE_WINNER",
                    "contract": "JNJ261023C00257500",
                    "ask": 1.1,
                }
            ]
        },
        "echo": {
            "option_flow": {
                "golden": [
                    {
                        "symbol": "LRCX",
                        "tier": "golden",
                        "flags": ["golden"],
                        "contract": "LRCX261023P00265000",
                        "right": "P",
                        "strike": 265,
                        "expiry": "2026-10-23",
                        "mid": 3.4,
                        "premium_notional": 250000,
                    },
                    {
                        "symbol": "LRCX",
                        "tier": "golden",
                        "contract": "LRCX261023P00265000",
                        "right": "P",
                        "strike": 265,
                        "expiry": "2026-10-23",
                        "mid": 3.4,
                    },
                ],
                "prints": [
                    {
                        "symbol": "AMD",
                        "tier": "unusual",
                        "flags": ["unusual"],
                        "contract": "AMD261023C00200000",
                        "right": "C",
                        "strike": 200,
                    },
                    {
                        "symbol": "XYZ",
                        "golden": True,
                        "pattern": "ascending triangle",
                        "contract": "",
                    },
                ],
            }
        },
        "chart_patterns": {
            "buy_pattern": [
                {"symbol": "TRI", "golden": True, "action": "BUY_PATTERN", "pattern": "golden triangle"}
            ]
        },
    }
    alerts = disp.collect_trade_alerts(snap)
    desks = [(a["desk"], a["symbol"]) for a in alerts]
    assert desks[0] == ("Gold Setup", "TSLA")
    assert ("Gold Setup", "F") not in desks
    assert ("Golden Flow", "LRCX") in desks
    assert sum(1 for desk, sym in desks if desk == "Golden Flow" and sym == "LRCX") == 1
    assert all(sym != "AMD" for _, sym in desks)
    assert all(sym != "XYZ" for _, sym in desks)
    assert all(sym != "TRI" for _, sym in desks)
    assert all(sym != "JNJ" for _, sym in desks)
    gold = alerts[0]["message"]
    assert gold.splitlines()[0] == "⭐ 🟢 SURE WINNER TSLA 385C 10/23 HIGH"
    flow = next(a for a in alerts if a["desk"] == "Golden Flow")
    assert flow["ask"] == 3.4
    assert flow["message"].splitlines()[0] == "🔴 GOLDEN FLOW LRCX 265P 10/23"


def test_gold_and_golden_are_not_crowded_out(tmp_path, monkeypatch):
    monkeypatch.delenv("CALLMEBOT_APIKEY", raising=False)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("WHATSAPP_TOKEN", raising=False)
    monkeypatch.setenv("WHATSAPP_TO", "15551234567")
    monkeypatch.setenv("CALLMEBOT_APIKEY", "123456")
    sent: list[str] = []

    def fake_send(body, **kwargs):
        sent.append(body)
        return {"ok": True, "provider": "callmebot", "configured": True}

    monkeypatch.setattr(disp, "send_whatsapp_text", fake_send)
    seen = tmp_path / "seen.json"
    seed = {
        "actions": {
            "buy_now": [
                {
                    "symbol": "MSFT",
                    "action": "BUY_NOW",
                    "contract": "MSFT1",
                    "signaled_at": "2026-10-20T15:00:00+00:00",
                }
            ]
        }
    }
    primed = disp.dispatch_snapshot_alerts(seed, seen_path=seen)
    assert primed.get("primed") is True

    rip = [
        {
            "symbol": f"R{i}",
            "action": "BUY_RIP",
            "contract": f"R{i}261023C00010000",
            "ask": 0.5,
            "signaled_at": f"2026-10-20T20:54:{i:02d}+00:00",
        }
        for i in range(10)
    ]
    snap = {
        "sure_winners": {
            "winners": [
                {
                    "symbol": "TSLA",
                    "action": "SURE_WINNER",
                    "sure_winner": True,
                    "confidence_tier": "MAX",
                    "contract": "TSLA261023C00385000",
                    "ask": 7.6,
                    "signaled_at": "2026-10-20T20:54:37+00:00",
                }
            ]
        },
        "echo": {
            "option_flow": {
                "prints": [
                    {
                        "symbol": "AMAT",
                        "tier": "golden",
                        "flags": ["golden"],
                        "contract": "AMAT261023C00420000",
                        "right": "C",
                        "strike": 420,
                        "expiry": "2026-10-23",
                        "mid": 2.2,
                    }
                ]
            }
        },
        "rip_radar": {"buy_rip": rip},
    }
    result = disp.dispatch_snapshot_alerts(snap, seen_path=seen)
    assert result.get("sent") == 8
    assert result.get("errors") in ([], None)
    assert "callmebot" in (result.get("providers") or [])
    assert any("SURE WINNER" in body and "TSLA" in body for body in sent)
    assert any("GOLDEN FLOW" in body and "AMAT" in body for body in sent)
    assert sent[0].splitlines()[0] == "⭐ 🟢 SURE WINNER TSLA 385C 10/23 MAX"
    assert sent[1].splitlines()[0] == "🟢 GOLDEN FLOW AMAT 420C 10/23"


def test_signal_headline_uses_sentiment_when_right_is_missing():
    bear = disp._fmt_alert(
        {"symbol": "SPY", "sentiment": "bearish", "strike": 500, "expiry": "2026-10-16", "ask": 2.0},
        "BUY",
        "Golden Flow",
    )
    bull = disp._fmt_alert(
        {
            "symbol": "QQQ",
            "sentiment": "bullish",
            "right": "C",
            "strike": 480.0,
            "expiry": "2026-10-16",
            "confidence_tier": "MAX",
            "ask": 1.5,
        },
        "BUY",
        "Gold Setup",
    )
    assert bear.splitlines()[0] == "🔴 GOLDEN FLOW SPY 500 10/16"
    assert bull.splitlines()[0] == "⭐ 🟢 SURE WINNER QQQ 480C 10/16 MAX"
    from_occ = disp._fmt_alert(
        {
            "symbol": "TSLA",
            "action": "SURE_WINNER",
            "confidence_tier": "HIGH",
            "contract": "TSLA261023P00385000",
            "ask": 7.6,
        },
        "BUY",
        "Gold Setup",
    )
    assert from_occ.splitlines()[0] == "⭐ 🔴 SURE WINNER TSLA 385P 10/23 HIGH"
