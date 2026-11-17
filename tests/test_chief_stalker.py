"""Regression tests for the read-only Whiteout Stats intelligence commands."""
from datetime import datetime, timezone

import pytest

from cogs.chief_stalker import basic_fields, describe_error, format_shield, with_include, arena_heroes, chief_equipment, ChiefStalker


def test_basic_fields_accepts_player_object():
    payload = {"player": {
        "nick_name": "Example Chief", "kid": 101, "furnace_level": 80,
        "power": 1298820795, "x": 12, "y": 345,
        "shield_endtime": 1794094142,
    }}
    result = basic_fields(payload)
    assert result == {
        "name": "Example Chief", "state": 101, "furnace": 80,
        "power": 1298820795, "x": 12, "y": 345, "shield": 1794094142,
        "kills": None, "vip": None, "alliance": None,
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
    import cogs.chief_stalker as module
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

    cog = ChiefStalker(FakeBot())
    result, error = await cog.get_player(123456789)
    assert error is None
    assert result["player"]["nick_name"] == "OK"
    assert seen == [(with_include(profile, ("base",)), "fake-key", 123456789)]


def test_with_include_replaces_section_without_mutating_original():
    p = {"player_path": "/v1/players/{id}?include=base&foo=bar"}
    updated = with_include(p, ("base", "heroes", "chief_gear"))
    assert p["player_path"] == "/v1/players/{id}?include=base&foo=bar"
    assert updated["player_path"] == "/v1/players/{id}?foo=bar&include=base%2Cheroes%2Cchief_gear"


def test_arena_heroes_are_not_full_inventory():
    payload = {"heroes": [{"name": "Hero A", "level": 80}, None, {"name": "Hero B"}]}
    assert len(arena_heroes(payload)) == 2
    assert arena_heroes({"heroes": None}) == []


def test_chief_equipment_hidden_vs_unavailable():
    assert chief_equipment({}) == (None, "Chief gear not reported")
    assert chief_equipment({"chief_gear": {"hidden": True, "items": [{"slot": 1}]}}) == (
        [], "Chief equipment hidden"
    )
    assert chief_equipment({"chief_gear": {"hidden": False, "items": [{"name": "Boots"}]}}) == (
        [{"name": "Boots"}], None
    )


@pytest.mark.asyncio
async def test_heroes_command_requests_only_relevant_sections(monkeypatch):
    import cogs.chief_stalker as module
    captured = []
    async def get_player(self, chief_id, include=("base",)):
        captured.append((chief_id, include))
        return {"heroes": [{"name": "Hero A", "level": 80, "skill_levels": [{"level": 5}]}]}, None
    monkeypatch.setattr(module.ChiefStalker, "get_player", get_player)
    sent = []
    class Response:
        async def defer(self, **kwargs):
            pass
    class Followup:
        async def send(self, *args, **kwargs):
            sent.append((args, kwargs))
    class Interaction:
        response = Response()
        followup = Followup()
    cog = ChiefStalker(object())
    await cog.wosheroes.callback(cog, Interaction(), "123456789")
    assert captured == [(123456789, ("base", "heroes"))]
    assert "embed" in sent[0][1]


def test_check_embed_reports_activity_and_profile():
    from cogs.chief_stalker import check_embed
    payload = {
        "player": {
            "nick_name": "Example Chief",
            "kid": 101,
            "furnace_level": 80,
            "power": 1303347544,
            "online": False,
            "last_login": "Last active 28m ago",
            "last_active_at": 1794608176.0,
            "alliance": {"abbr": "ERA", "name": "NewEra"},
            "x": 774, "y": 815,
        },
        "age_seconds": 1602,
    }
    embed = check_embed(payload, 123456789)
    assert embed is not None
    rendered = {field.name: field.value for field in embed.fields}
    assert rendered["Activity status"] == "⚪ Reported offline"
    assert rendered["Reported last active"] == "<t:1794608176:R>"
    assert rendered["Recorded activity time (UTC)"] == "2026-11-13 22:16:16 UTC"
    assert rendered["Alliance"] == "[ERA] NewEra"
    assert rendered["Last reported coordinates"] == "X: 774 · Y: 815"
    assert "stale" in embed.footer.text


def test_check_activity_unknown_is_not_offline():
    from cogs.chief_stalker import check_embed, activity_timestamp
    embed = check_embed({"player": {"nick_name": "Test"}}, 123)
    rendered = {field.name: field.value for field in embed.fields}
    assert rendered["Activity status"] == "Not reported"
    assert rendered["Reported last active"] == "Not reported"
    assert "Recorded activity time (UTC)" not in rendered
    assert activity_timestamp(True) is None
    assert activity_timestamp(-1) is None
    assert activity_timestamp("1794608176") is None


@pytest.mark.asyncio
async def test_check_command_reuses_client_and_shows_refresh(monkeypatch):
    from cogs.chief_stalker import ChiefStalker, CheckRefreshView
    seen = []
    async def fake_get(self, chief_id, include=("base",)):
        seen.append((chief_id, include))
        return {"player": {"nick_name": "Chief", "online": True}}, None
    monkeypatch.setattr(ChiefStalker, "get_player", fake_get)
    class User:
        id = 42
    class Response:
        async def defer(self, **kwargs):
            assert kwargs.get("ephemeral") is not True
    class Followup:
        async def send(self, **kwargs):
            assert kwargs.get("ephemeral") is not True
            assert isinstance(kwargs["view"], CheckRefreshView)
            assert kwargs["view"].requester_id == 42
            assert kwargs["embed"].fields[0].value == "🟢 Reported online"
    class Interaction:
        user = User()
        response = Response()
        followup = Followup()
    cog = ChiefStalker(object())
    await cog.check.callback(cog, Interaction(), "123456789")
    assert seen == [(123456789, ("base",))]


def test_check_utc_is_independent_of_local_timezone(monkeypatch):
    import os
    import time
    from cogs.chief_stalker import check_embed
    payload = {"player": {"nick_name": "Example", "last_active_at": 1794608176.0}}
    previous_tz = os.environ.get("TZ")
    try:
        for tz in ("UTC", "Pacific/Auckland", "America/Los_Angeles"):
            monkeypatch.setenv("TZ", tz)
            if hasattr(time, "tzset"):
                time.tzset()
            embed = check_embed(payload, 123456789)
            fields = {field.name: field.value for field in embed.fields}
            assert fields["Recorded activity time (UTC)"] == "2026-11-13 22:16:16 UTC"
            assert fields["Reported last active"] == "<t:1794608176:R>"
    finally:
        if previous_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", previous_tz)
        if hasattr(time, "tzset"):
            time.tzset()


def test_check_unifies_player_shield_and_original_action_buttons():
    from cogs.chief_stalker import CheckRefreshView, check_embed
    embed = check_embed({"player": {
        "nick_name": "Example", "online": True, "shield_endtime": 1794094142,
        "kid": 101, "power": 1000,
    }}, 12345)
    fields = {field.name: field.value for field in embed.fields}
    assert "Shield" in fields
    assert "Reported expiry" in fields["Shield"] or "Last reported expiry" in fields["Shield"]
    view = CheckRefreshView(object(), 12345, 7)
    labels = {button.label for button in view.children}
    assert {"Stored Profile", "Player History", "Arena Heroes",
            "Chief Gear", "Refresh"} <= labels
    assert len(view.children) <= 25


@pytest.mark.asyncio
async def test_check_saves_snapshot_without_replacing_w(monkeypatch):
    from types import SimpleNamespace
    from cogs.chief_stalker import ChiefStalker
    payload = {"player": {"nick_name": "Example", "online": False,
                          "power": 5000}, "cached_at": 1794608176}
    observed = []
    async def get_player(self, chief_id, include=("base",)):
        return payload, None
    async def save(self, chief_id, data):
        observed.append((chief_id, data))
    monkeypatch.setattr(ChiefStalker, "get_player", get_player)
    monkeypatch.setattr(ChiefStalker, "save_observation", save)
    sent = []
    async def defer(**kwargs):
        return None
    async def send(**kwargs):
        sent.append(kwargs)
    cog = ChiefStalker(SimpleNamespace())
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=7),
        response=SimpleNamespace(defer=defer),
        followup=SimpleNamespace(send=send),
    )
    await cog.check.callback(cog, interaction, "12345")
    assert observed == [(12345, payload)]
    assert sent[0]["embed"].fields[-1].name == "Shield"
    assert sent[0]["view"].chief_id == 12345


def test_w_command_source_is_unchanged_by_unification():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "cogs" / "alliance_w_command.py"
    source = path.read_text()
    assert "async def fetch_user_info(" in source
    assert "async def _live_profile_embed(" in source
    assert "PermissionManager.is_admin" in source
