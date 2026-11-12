"""Regression tests for daily full-data observations and change tracking."""
import json
from pathlib import Path
from cogs.wos_observations import save_observation, fetch_changes


def test_location_state_and_nested_hero_gear_history(tmp_path):
    db = str(tmp_path / "observations.sqlite")
    old = {"player": {"kid": 1501, "x": 517, "y": 444, "nick_name": "A"},
           "heroes": [{"name": "Gregory", "gear": [{"plus": 79, "mastery": 19}]}]}
    new = {"player": {"kid": 1502, "x": 10, "y": 21, "nick_name": "B"},
           "heroes": [{"name": "Gregory", "gear": [{"plus": 100, "mastery": 20}]}]}
    assert save_observation(137940156, old, path=db) == 0
    assert save_observation(137940156, new, path=db) == 6
    history = {field: (json.loads(before), json.loads(after))
               for _, field, before, after in fetch_changes(137940156, path=db)}
    assert history["player.kid"] == (1501, 1502)
    assert history["player.x"] == (517, 10)
    assert history["player.y"] == (444, 21)
    assert history["player.nick_name"] == ("A", "B")
    assert history["heroes[0].gear[0].plus"] == (79, 100)
    assert history["heroes[0].gear[0].mastery"] == (19, 20)


def test_omitted_sections_and_coordinates_do_not_erase_last_seen(tmp_path):
    db = str(tmp_path / "observations.sqlite")
    first = {"player": {"kid": 1501, "x": 517, "y": 444, "power": 100},
             "heroes": [{"gear": [{"plus": 50}]}]}
    save_observation(11, first, path=db)
    assert save_observation(11, {"player": {"power": 200}}, path=db) == 1
    assert [r[1] for r in fetch_changes(11, path=db)] == ["player.power"]
    with __import__("sqlite3").connect(db) as conn:
        stored = json.loads(conn.execute(
            "SELECT payload_json FROM observations ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert stored["player"]["x"] == 517
    assert stored["player"]["kid"] == 1501
    assert stored["heroes"][0]["gear"][0]["plus"] == 50


def test_volatile_cache_age_is_not_logged_as_game_change(tmp_path):
    db = str(tmp_path / "observations.sqlite")
    save_observation(33, {"player": {"kid": 1501}, "age_seconds": 100}, path=db)
    assert save_observation(33, {"player": {"kid": 1501}, "age_seconds": 130}, path=db) == 0


def test_player_sync_uses_calendar_day_and_full_api_sections():
    source = (Path(__file__).resolve().parents[1] / "cogs" / "alliance_api_sync.py").read_text()
    assert "@tasks.loop(minutes=1)" in source
    assert "last_daily_date" in source
    assert "now.date().isoformat()" in source
    assert "save_observation, fid, answer.payload" in source
    assert "rich_profile = with_include(profile, (\u0027base\u0027, \u0027heroes\u0027, \u0027chief_gear\u0027))" in source
