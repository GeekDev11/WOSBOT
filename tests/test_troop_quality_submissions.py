"""Screenshot recognition and ownership tests; no Discord API required."""
import sqlite3

import pytest

from cogs import troop_quality as tq
from cogs import troop_quality_submissions as sub


def test_parse_tiers_from_troops_preview_ignores_counts():
    text = """Exalted Infantry 456,444 Exalted Marksman 1,281,665
              Helios Infantry 1,459,230 Helios Lancer 1,188,606
              Helios Marksman 485,523 Apex Lancer 429,928"""
    assert sub.parse_tiers(text) == {
        "infantry": 12, "lancer": 11, "marksman": 12
    }


def test_parse_single_chief_id():
    assert sub.parse_chief_id("137940156") == 137940156
    assert sub.parse_chief_id("ID: 137940156\nAttached screenshot") == 137940156
    assert sub.parse_chief_id("137940156 and 454298509") is None
    assert sub.parse_chief_id("not an ID") is None


def test_skills_and_preserved_values():
    tiers = {"infantry": 12, "lancer": 11, "marksman": 12}
    result = sub.submission_changes(
        tiers, {"infantry": 0, "marksman": 3},
        {"lancer": (12, 2)}
    )
    assert result == {"infantry": (12, 0), "marksman": (12, 3)}
    with pytest.raises(ValueError):
        sub.submission_changes({"infantry": 12}, {})
    assert sub.submission_changes({"infantry": 10}, {}, {"infantry": (11, None)}) == {}


def test_member_cannot_submit_another_chiefs_id(tmp_path):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, alliance TEXT, discord_id INTEGER)")
        db.executemany("INSERT INTO users VALUES (?,?,?)", [
            (1234567, "920", 42), (2345678, "920", None), (3456789, "921", 42)
        ])
    assert sub.owner_matches(1234567, 920, 42, path)
    assert not sub.owner_matches(1234567, 920, 99, path)
    assert not sub.owner_matches(2345678, 920, 42, path)
    assert not sub.owner_matches(3456789, 920, 42, path)
