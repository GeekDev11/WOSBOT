"""Regression coverage for manual troop quality and atomic bulk updates."""
import sqlite3
from datetime import datetime

import pytest

from cogs import troop_quality as tq


def _db(tmp_path):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, nickname TEXT, alliance TEXT)")
        db.executemany("INSERT INTO users VALUES (?,?,?)", [
            (101, "Alpha", "920"), (102, "Bravo", "920"), (103, "Charlie", "921")
        ])
    return path


def test_t12_skill_validation_and_utc_history(tmp_path):
    path = _db(tmp_path)
    tq.initialize(path)
    assert tq.save_quality(920, [101], {
        "infantry": (12, 3), "lancer": (11, None),
        "marksman": (None, None)}, 777, path) == 2
    q = tq.read_quality([101], path)[101]
    assert q == {"infantry": (12, 3), "lancer": (11, None),
                 "marksman": (None, None)}
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT troop_type, changed_at, changed_by FROM "
                          "member_troop_quality_history ORDER BY id").fetchall()
        assert len(rows) == 2
        assert all(row[2] == 777 for row in rows)
        assert all(datetime.fromisoformat(row[1]).utcoffset().total_seconds() == 0 for row in rows)
    with pytest.raises(ValueError):
        tq.save_quality(920, [101], {"infantry": (12, 4)}, 777, path)
    assert tq.read_quality([101], path)[101]["infantry"] == (12, 3)


def test_bulk_edits_preserve_unselected_troop_types(tmp_path):
    path = _db(tmp_path)
    tq.save_quality(920, [101], {"marksman": (12, 2)}, 777, path)
    assert tq.save_quality(920, [101, 102], {"infantry": (12, 1)}, 888, path) == 2
    current = tq.read_quality([101, 102], path)
    assert current[101]["marksman"] == (12, 2)
    assert current[101]["infantry"] == (12, 1)
    assert current[102]["infantry"] == (12, 1)
    assert tq.save_quality(920, [101], {"infantry": (12, 1)}, 888, path) == 0


def test_bulk_transaction_rejects_removed_or_transferred_members(tmp_path):
    path = _db(tmp_path)
    with pytest.raises(ValueError, match="Member selection changed"):
        tq.save_quality(920, [101, 103], {"infantry": (11, None)}, 5, path)
    assert tq.read_quality([101, 103], path) == {}


def test_record_follows_chief_on_alliance_transfer(tmp_path):
    path = _db(tmp_path)
    tq.save_quality(920, [101], {"lancer": (12, 2)}, 44, path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE users SET alliance='921' WHERE fid=101")
    assert tq.read_quality([101], path)[101]["lancer"] == (12, 2)
    assert tq.save_quality(921, [101], {"lancer": (12, 3)}, 55, path) == 1


def test_preview_and_csv_export(tmp_path):
    path = _db(tmp_path)
    tq.save_quality(920, [101], {"infantry": (12, 3)}, 99, path)
    assert tq.format_tier(12, 3) == "T12 · Skill 3/3"
    assert tq.format_tier(None, None) == "Unknown"
    assert tq.format_tier(0, None) == "Below T11"
    exported = tq.export_csv(920, path).decode("utf-8-sig")
    assert "Alpha,101,12,3" in exported
    assert "Bravo,102" in exported
    assert "Charlie" not in exported


def test_editor_options_cover_all_three_types():
    assert set(tq.TROOPS) == {"infantry", "lancer", "marksman"}
    assert tq.CHOICES["t12_1"] == (12, 1)
    assert tq.CHOICES["t12_2"] == (12, 2)
    assert tq.CHOICES["t12_3"] == (12, 3)


@pytest.mark.asyncio
async def test_permission_check_blocks_other_alliances(monkeypatch):
    monkeypatch.setattr(tq.PermissionManager, "get_admin_alliance_ids",
                        lambda uid,gid: ([920], False))
    assert tq.allowed(1, 2, 920)
    assert not tq.allowed(1, 2, 921)
