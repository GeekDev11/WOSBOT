"""Regression tests for safe API roster imports (no live API calls)."""
import sqlite3
from types import SimpleNamespace

import pytest

from cogs import alliance_roster_import as roster


RESOLVED = SimpleNamespace(state_id=1501, aid=321, tag="04A")


def payload(members, count=None):
    return {
        "ok": True, "fresh": True,
        "alliance": {"kid": 1501, "aid": 321, "abbr": "04A",
                     "count": len(members) if count is None else count},
        "members": members,
    }


def member(fid, name="Player"):
    return {"chief_id": fid, "nick_name": name, "kid": 1501,
            "power": 100000, "furnace_level": 30}


def test_parse_valid_roster():
    result = roster.parse_roster(payload([member(101), member(102)]), RESOLVED)
    assert [r[0] for r in result] == [101, 102]


@pytest.mark.parametrize("change", [
    lambda p: p.update(fresh=False),
    lambda p: p["alliance"].update(abbr="04a"),
    lambda p: p["alliance"].update(kid=920),
    lambda p: p["alliance"].update(aid=999),
    lambda p: p["alliance"].update(count=2),
    lambda p: p["members"].append(member(101)),
])
def test_bad_roster_is_rejected(change):
    data = payload([member(101)])
    change(data)
    with pytest.raises(ValueError):
        roster.parse_roster(data, RESOLVED)


def test_safe_additive_import_preserves_existing_home(tmp_path, monkeypatch):
    alliance_db = tmp_path / "alliance.sqlite"
    users_db = tmp_path / "users.sqlite"
    monkeypatch.setattr(roster, "ALLIANCE_DB", str(alliance_db))
    monkeypatch.setattr(roster, "USERS_DB", str(users_db))
    with sqlite3.connect(alliance_db) as db:
        db.execute("CREATE TABLE alliance_list (alliance_id INTEGER, discord_server_id INTEGER, kid INTEGER, game_aid INTEGER, game_tag TEXT)")
        db.executemany("INSERT INTO alliance_list VALUES (?,?,?,?,?)", [
            (10, 123, 1501, 321, "04A"), (11, 123, 1501, 500, "VEN")])
    with sqlite3.connect(users_db) as db:
        db.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY,nickname TEXT,furnace_lv INTEGER,kid INTEGER,stove_lv_content TEXT,alliance INTEGER,power INTEGER,power_updated_at TEXT)")
        db.execute("INSERT INTO users(fid,nickname,alliance) VALUES (101,'VEN visitor',11)")
        db.execute("INSERT INTO users(fid,nickname,alliance) VALUES (102,'Already 04A',10)")
    entries = roster.parse_roster(payload([member(101), member(102), member(103)]), RESOLVED)
    report = roster.import_roster(123, 10, RESOLVED, entries)
    assert (report.received, report.added, report.already_home, report.conflicts) == (3, 1, 1, 1)
    with sqlite3.connect(users_db) as db:
        assert db.execute("SELECT alliance FROM users WHERE fid=101").fetchone()[0] == 11
        assert db.execute("SELECT alliance FROM users WHERE fid=103").fetchone()[0] == 10
    with pytest.raises(ValueError):
        roster.import_roster(999, 10, RESOLVED, entries)
