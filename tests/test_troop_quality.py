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


def test_t10_and_t12_skill_zero_supported(tmp_path):
    path = _db(tmp_path)
    assert tq.save_quality(920, [101], {
        "infantry": (10, None), "lancer": (12, 0)}, 77, path) == 2
    rows = tq.read_quality([101], path)
    assert rows[101]["infantry"] == (10, None)
    assert rows[101]["lancer"] == (12, 0)
    assert tq.format_tier(10, None) == "T10"
    assert tq.format_tier(12, 0) == "T12 · Skill 0/3"


def test_existing_troop_quality_rows_survive_schema_migration(tmp_path):
    path = _db(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("""
            CREATE TABLE member_troop_quality (
                fid INTEGER PRIMARY KEY,
                infantry_tier INTEGER, infantry_skill INTEGER,
                lancer_tier INTEGER, lancer_skill INTEGER,
                marksman_tier INTEGER, marksman_skill INTEGER,
                updated_at TEXT NOT NULL, updated_by INTEGER NOT NULL,
                CHECK(infantry_tier IS NULL OR infantry_tier IN (0, 11, 12)),
                CHECK(lancer_tier IS NULL OR lancer_tier IN (0, 11, 12)),
                CHECK(marksman_tier IS NULL OR marksman_tier IN (0, 11, 12)),
                CHECK((infantry_tier=12 AND infantry_skill BETWEEN 1 AND 3)
                      OR (infantry_tier IS NOT 12 AND infantry_skill IS NULL)),
                CHECK((lancer_tier=12 AND lancer_skill BETWEEN 1 AND 3)
                      OR (lancer_tier IS NOT 12 AND lancer_skill IS NULL)),
                CHECK((marksman_tier=12 AND marksman_skill BETWEEN 1 AND 3)
                      OR (marksman_tier IS NOT 12 AND marksman_skill IS NULL))
            )
        """)
        db.execute("INSERT INTO member_troop_quality "
                   "VALUES (101, 12, 2, 11, NULL, NULL, NULL, '2026-11-14T00:00:00+00:00', 1)")
    tq.initialize(path)
    assert tq.read_quality([101], path)[101]["infantry"] == (12, 2)
    assert tq.save_quality(920, [101], {"infantry": (12, 0)}, 77, path) == 1


def test_submission_channel_settings_are_unique_and_clearable(tmp_path):
    path = tmp_path / "alliance.sqlite"
    tq.set_submission_channel(920, 100, 300, 55, path)
    assert tq.submission_alliance(100, 300, path) == 920
    assert tq.submission_channel(920, path) == 300
    with pytest.raises(ValueError):
        tq.set_submission_channel(921, 100, 300, 55, path)
    tq.clear_submission_channel(920, path)
    assert tq.submission_alliance(100, 300, path) is None
