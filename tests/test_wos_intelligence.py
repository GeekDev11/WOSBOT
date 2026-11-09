"""Regression tests for the read-only Whiteout Stats intelligence commands."""
from datetime import datetime, timezone

import pytest

from cogs.wos_intelligence import basic_fields, describe_error, format_shield, WosIntelligence


def test_basic_fields_accepts_player_object():
    payload = {"player": {
        "nick_name": "FUTURE IS BRIGHT", "kid": 1501, "furnace_level": 80,
        "power": 1298820795, "x": 12, "y": 345,
        "shield_endtime": 1794094142,
    }}
    result = basic_fields(payload)
    assert result == {
        "name": "FUTURE IS BRIGHT", "state": 1501, "furnace": 80,
        "power": 1298820795, "x": 12, "y": 345, "shield": 1794094142,
    }


@pytest.mark.parametrize("payload", [None, {}, [], {"player": None}, {"player": []}])
def test_basic_fields_missing_or_malformed(payload):
    assert basic_fields(payload) == {}


def test_missing_optional_fields_stay_none():
    result = basic_fields({"player": {"nick_name": "Test"}})
    assert result["x"] is None and result["y"] is None
    assert result["shield"] is None


def test_shield_expiry_renders_discord_timestamps():
    assert format_shield(1794094142, now=1794094000) == (
        "Reported expiry: <t:1794094142:F> (<t:1794094142:R>)"
    )
    assert format_shield(1794094142, now=1794095000).startswith("Last reported expiry:")


@pytest.mark.parametrize("value", [None, 0, "", "abc", True, -10])
def test_missing_invalid_shield_values(value):
    assert format_shield(value, now=1) in ("Not reported", "Invalid API value")


@pytest.mark.parametrize("status", [0, 401, 403, 404, 429, 500])
def test_errors_do_not_reveal_key(status):
    assert "wos_" not in describe_error(status)


@pytest.mark.asyncio
async def test_get_player_uses_existing_connection_and_quota(monkeypatch):
    import cogs.wos_intelligence as module
    from cogs.alliance_api_sync import FetchResult

    profile = {
        "base_url": "https://api.whiteoutstats.com",
        "player_path": "/v1/players/{id}?include=base",
        "auth_header": "X-Api-Key",
        "per_minute": 30, "per_day": 1000,
        "fields": {"name": "player.nick_name", "level": "", "kingdom": "", "power": "", "avatar": ""},
    }
    monkeypatch.setattr(module.sync, "load_connection", lambda: (profile, "fake-key"))
    async def slot(_profile):
        return True
    monkeypatch.setattr(module.sync, "_take_slot", slot)
    seen = []
    async def fake_fetch(session, p, key, fid):
        seen.append((p, key, fid))
        return FetchResult(200, {"player": {"nick_name": "OK"}})
    monkeypatch.setattr(module.sync, "fetch_player", fake_fetch)

    class FakeSync:
        def session(self):
            return object()

    class FakeBot:
        def get_cog(self, name):
            return FakeSync() if name == "AllianceApiSync" else None

    cog = WosIntelligence(FakeBot())
    result, error = await cog.get_player(137940156)
    assert error is None
    assert result["player"]["nick_name"] == "OK"
    assert seen == [(profile, "fake-key", 137940156)]
