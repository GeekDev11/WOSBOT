"""Tests for persistent Whiteout Stats player history."""
import sqlite3

from cogs.wos_player_history import delta_text, load_history, store_player


def profile(power=100, kills=5, ts=100):
    return {
        "cached_at": ts,
        "player": {
            "nick_name": "Tester", "kid": 1501, "power": power,
            "kills": kills, "furnace_level": 80, "vip": 11
        }
    }


def test_history_stores_distinct_provider_observations(tmp_path):
    path = tmp_path / "db" / "whiteoutstats.sqlite"
    assert store_player(123, profile(), path)
    assert not store_player(123, profile(), path)
    assert store_player(123, profile(200, 8, 200), path)
    rows = load_history(123, path=path)
    assert len(rows) == 2
    assert "**Power:** +100" in delta_text(rows)
    assert "**Kills:** +3" in delta_text(rows)


def test_invalid_snapshot_does_not_create_db(tmp_path):
    path = tmp_path / "db" / "whiteoutstats.sqlite"
    assert not store_player(123, {"player": {"power": 100}}, path)
    assert not path.exists()


def test_missing_metrics_never_become_zero(tmp_path):
    path = tmp_path / "db" / "whiteoutstats.sqlite"
    assert store_player(123, profile(ts=100), path)
    assert store_player(123, {"cached_at": 200, "player": {"power": 110}}, path)
    rows = load_history(123, path=path)
    assert "Kills:" not in delta_text(rows)
    assert "**Power:** +10" in delta_text(rows)


def test_first_observation_has_no_growth_claim(tmp_path):
    path = tmp_path / "db" / "whiteoutstats.sqlite"
    assert store_player(123, profile(), path)
    assert "at least two" in delta_text(load_history(123, path=path))
