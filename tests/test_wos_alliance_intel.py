"""Alliance intelligence: snapshots are safe and only complete rosters produce events."""
import sqlite3

import pytest

from cogs.wos_alliance_intel import (
    parse_alliance, diff_rosters, init_db, recent_events, store_roster,
)


def sample(members, cached_at=100):
    return {
        "ok": True,
        "alliance": {"aid": 1234, "kid": 1501, "abbr": "ABC", "name": "Test Alliance"},
        "members": members,
        "cached_at": cached_at,
    }


def test_roster_validated_and_normalized():
    info, roster = parse_alliance(sample([
        {"chief_id": 101, "nick_name": "Alpha", "power": 1000, "alliance_rank_label": "R4"},
        {"chief_id": 102, "nick_name": "Beta", "power": 500, "alliance_rank_label": "R2"},
    ]))
    assert info["aid"] == 1234
    assert roster[101]["rank"] == "R4"
    assert len(roster) == 2


@pytest.mark.parametrize("payload", [
    {}, {"alliance": {"aid": 1, "kid": 1501}},
    {"alliance": {"aid": 1, "kid": 1501}, "members": None},
    sample([{"nick_name": "No ID"}]),
    sample([{"chief_id": 1}, {"chief_id": 1}]),
])
def test_partial_roster_is_never_treated_as_a_complete_snapshot(payload):
    with pytest.raises(ValueError):
        parse_alliance(payload)


def test_diff_does_not_call_departure_a_state_transfer():
    before = {101: {"rank": "R1"}, 102: {"rank": "R2"}}
    after = {102: {"rank": "R3"}, 103: {"rank": "R1"}}
    assert diff_rosters(before, after) == {
        "joined": [103], "left": [101], "rank_changed": [102]
    }


def test_first_observation_is_baseline_and_subsequent_observations_make_events(tmp_path):
    db = tmp_path / "db" / "whiteoutstats.sqlite"
    info1, roster1 = parse_alliance(sample([{"chief_id": 101, "alliance_rank": 4}]))
    first = store_roster(1501, info1, roster1, 100, db)
    assert first["reason"] == "initial baseline"
    assert first["events"] is None

    same = store_roster(1501, info1, roster1, 100, db)
    assert same["stored"] is False

    info2, roster2 = parse_alliance(sample([
        {"chief_id": 101, "alliance_rank": 3}, {"chief_id": 102, "alliance_rank": 1}
    ]))
    next_result = store_roster(1501, info2, roster2, 200, db)
    assert next_result["events"] == {
        "joined": [102], "left": [], "rank_changed": [101]
    }
    rows = recent_events(1501, 1234, path=db)
    assert {r[1] for r in rows} == {"joined", "rank_changed"}

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM wos_alliance_snapshots").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM wos_alliance_events").fetchone()[0] == 2


def test_empty_roster_is_valid_when_explicitly_provided():
    info, roster = parse_alliance(sample([]))
    assert roster == {}


def test_invalid_roster_does_not_touch_database(tmp_path):
    db = tmp_path / "db" / "whiteoutstats.sqlite"
    with pytest.raises(ValueError):
        parse_alliance({"alliance": {"aid": 1, "kid": 1501}})
    assert not db.exists()
