"""Membership tracking must preserve the managed home roster during temporary visits."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from cogs import membership_service as m


@pytest.fixture
def dbs(tmp_path, monkeypatch):
    tracking = tmp_path / "membership.sqlite"
    alliance = tmp_path / "alliance.sqlite"
    users = tmp_path / "users.sqlite"
    monkeypatch.setattr(m, "DB", str(tracking))
    monkeypatch.setattr(m, "ALLIANCE_DB", str(alliance))
    monkeypatch.setattr(m, "USERS_DB", str(users))
    with sqlite3.connect(alliance) as db:
        db.execute("CREATE TABLE alliance_list (alliance_id INTEGER,discord_server_id INTEGER,kid INTEGER,name TEXT)")
        db.executemany("INSERT INTO alliance_list VALUES (?,?,?,?)",
                       [(1,10,1501,"VEN"),(2,10,1501,"ABC"),(3,20,1600,"XYZ")])
    with sqlite3.connect(users) as db:
        db.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY,alliance INTEGER,nickname TEXT)")
        db.execute("INSERT INTO users VALUES (101,1,'Chief')")
    m.initialize()
    m.set_enabled(10, True)
    return tracking, alliance, users


def player(state, alliance, tag):
    return {"player":{"kid":state,"alliance":{"aid":alliance,"abbr":tag}}}


def stamp(n):
    return (datetime(2026,11,19,tzinfo=timezone.utc)+timedelta(minutes=n)).isoformat()


def test_approved_temporary_visit_does_not_move_home_member(dbs):
    m.add_temporary(10,1501,9001,"S01",42)
    assert m.observe(101,1,player(1501,1,"VEN"),stamp(0))=="baseline"
    assert m.observe(101,1,player(1501,9001,"S01"),stamp(1))=="temporary"
    assert m.dashboard(10,[1])=={"active":1,"visiting":1,"pending":0,"transfers":0}
    with sqlite3.connect(dbs[2]) as db:
        assert db.execute("SELECT alliance FROM users WHERE fid=101").fetchone()[0]==1
    assert m.observe(101,1,player(1501,1,"VEN"),stamp(2))=="home"
    assert m.dashboard(10,[1])["visiting"]==0


def test_unregistered_alliance_creates_one_review_case(dbs):
    assert m.observe(101,1,player(1501,1,"VEN"),stamp(0))=="baseline"
    assert m.observe(101,1,player(1501,777,"XYZ"),stamp(1))=="review"
    assert m.observe(101,1,player(1501,777,"XYZ"),stamp(2))=="review"
    cases=m.list_cases([1])
    assert len(cases)==1
    assert cases[0]["category"]=="review"
    assert m.dashboard(10,[1])["active"]==1


def test_state_transfer_requires_review_even_when_tag_approved(dbs):
    m.add_temporary(10,1501,9001,"S01",42)
    m.observe(101,1,player(1501,1,"VEN"),stamp(0))
    assert m.observe(101,1,player(1600,9001,"S01"),stamp(1))=="state_transfer"
    assert m.dashboard(10,[1])["transfers"]==1


def test_alliance_scoped_rule_does_not_apply_to_other_home(dbs):
    m.add_temporary(10,1501,9001,"S01",42,scope_alliance_id=2)
    m.observe(101,1,player(1501,1,"VEN"),stamp(0))
    assert m.observe(101,1,player(1501,9001,"S01"),stamp(1))=="review"


def test_individual_temporary_resolution_preserves_home(dbs):
    m.observe(101,1,player(1501,1,"VEN"),stamp(0))
    m.observe(101,1,player(1501,777,"XYZ"),stamp(1))
    case=m.list_cases([1])[0]
    assert not m.decide(case["id"],case["version"],99,[2],"temporary")
    assert m.decide(case["id"],case["version"],99,[1],"temporary")
    assert len(m.list_cases([1]))==0
    assert m.dashboard(10,[1])["visiting"]==1


def test_stale_observation_cannot_reverse_visit(dbs):
    m.add_temporary(10,1501,9001,"S01",42)
    m.observe(101,1,player(1501,1,"VEN"),stamp(0))
    m.observe(101,1,player(1501,9001,"S01"),stamp(2))
    assert m.observe(101,1,player(1501,1,"VEN"),stamp(1))=="stale"
    assert m.dashboard(10,[1])["visiting"]==1


def test_disabled_server_cannot_track_or_remove(dbs):
    m.set_enabled(10,False)
    assert m.observe(101,1,player(1600,777,"XYZ"),stamp(0))=="disabled"
    assert m.dashboard(10,[1])["active"]==1
    assert not m.list_cases([1])


def test_move_changes_only_home_roster_with_versioned_case(dbs):
    m.observe(101,1,player(1501,1,"VEN"),stamp(0))
    m.observe(101,1,player(1501,2,"ABC"),stamp(1))
    case=m.list_cases([1])[0]
    assert not m.apply_roster_action(case["id"],case["version"],42,[1],"move",2,[99])
    assert m.apply_roster_action(case["id"],case["version"],42,[1],"move",2,[2])
    assert not m.apply_roster_action(case["id"],case["version"],42,[1],"move",2,[2])
    with sqlite3.connect(dbs[2]) as db:
        assert db.execute("SELECT alliance FROM users WHERE fid=101").fetchone()[0]==2
    assert m.list_history([1])[0]["decision"]=="move"


def test_archive_preserves_recoverable_snapshot(dbs):
    m.observe(101,1,player(1501,1,"VEN"),stamp(0))
    m.observe(101,1,player(1600,77,"XYZ"),stamp(1))
    case=m.list_cases([1])[0]
    assert not m.apply_roster_action(case["id"],case["version"]+1,42,[1],"archive")
    assert m.apply_roster_action(case["id"],case["version"],42,[1],"archive")
    with sqlite3.connect(dbs[2]) as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE fid=101").fetchone()[0]==0
    with sqlite3.connect(dbs[0]) as db:
        row=db.execute("SELECT snapshot_json FROM retired_members WHERE fid=101").fetchone()
    assert '"nickname": "Chief"' in row[0]
    assert m.list_history([1])[0]["decision"]=="archive"


def test_state_transfer_cannot_be_marked_temporary(dbs):
    m.observe(101,1,player(1501,1,"VEN"),stamp(0))
    m.observe(101,1,player(1600,77,"XYZ"),stamp(1))
    case=m.list_cases([1])[0]
    assert not m.decide(case["id"],case["version"],42,[1],"temporary")
    assert len(m.list_cases([1]))==1


def test_resolved_keep_does_not_create_duplicate_alert(dbs):
    m.observe(101,1,player(1501,1,"VEN"),stamp(0))
    m.observe(101,1,player(1501,777,"XYZ"),stamp(1))
    case=m.list_cases([1])[0]
    assert m.decide(case["id"],case["version"],99,[1],"keep")
    assert m.observe(101,1,player(1501,777,"XYZ"),stamp(2))=="kept"
    assert m.list_cases([1])==[]


def test_individual_temporary_remains_exempt_on_next_sync(dbs):
    m.observe(101,1,player(1501,1,"VEN"),stamp(0))
    m.observe(101,1,player(1501,777,"XYZ"),stamp(1))
    case=m.list_cases([1])[0]
    assert m.decide(case["id"],case["version"],42,[1],"temporary")
    assert m.observe(101,1,player(1501,777,"XYZ"),stamp(2))=="temporary"
    assert m.list_cases([1])==[]
