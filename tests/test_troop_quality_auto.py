"""Troop levels are provided as tier; FC star and troop count are irrelevant."""
import sqlite3
import pytest

from cogs import troop_quality as quality
from cogs import troop_quality_auto as auto


def test_expedition_troops_tier_not_star():
    player = {"expedition_troops": [
        {"kind": "inf", "id": 11200, "tier": 12, "star": 9, "count": 100},
        {"kind": "lan", "id": 21200, "tier": 12, "star": 10, "count": 0},
        {"kind": "mm", "id": 31100, "tier": 11, "star": 10, "count": 100000},
        {"kind": "inf", "id": 11100, "tier": 11, "star": 10, "count": 999999},
    ]}
    assert auto.extract_tiers(player) == {
        "infantry": 12, "lancer": 12, "marksman": 11
    }
    assert auto.extract_tiers({"expedition_troops": None}) == {}
    assert auto.extract_tiers({"expedition_troops": [{"kind": "inf", "tier": 9}]}) == {}


def test_merge_preserves_skill_and_unknown():
    assert auto.merge_tiers(
        {"infantry": 12, "lancer": 12, "marksman": 10},
        {"infantry": (12, 3), "lancer": (11, None)}
    ) == {"lancer": (12, None), "marksman": (10, None)}
    assert auto.merge_tiers({"infantry": 11}, {"infantry": (12, 0)}) == {}
    assert auto.merge_tiers({}, {"infantry": (12, 0)}) == {}


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status = data, status
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def json(self, **kwargs):
        return self.data


class FakeSession:
    def __init__(self, search, profile):
        self.search, self.profile = search, profile
        self.urls = []
    def get(self, url, **kwargs):
        self.urls.append((url, kwargs.get("params")))
        if url.endswith("/api/search"):
            return FakeResponse(self.search)
        return FakeResponse(self.profile)


@pytest.mark.asyncio
async def test_fetch_website_uid_mapping_is_exact():
    s = FakeSession(
        {"results": [
            {"uid": 123, "fid": 555},
            {"uid": 33763165, "fid": 137940156},
        ]},
        {"fid": 137940156, "expedition_troops": [
            {"kind": "inf", "tier": 12, "star": 9, "count": 1}
        ]},
    )
    assert await auto.fetch_tiers(s, 137940156) == {"infantry": 12}
    assert s.urls[0][1] == {"q": "137940156", "limit": "10", "live": "0"}
    assert s.urls[1][0].endswith("/api/players/33763165")


@pytest.mark.asyncio
async def test_unknown_fid_does_not_request_another_players_profile():
    s = FakeSession({"results": [{"uid": 33763165, "fid": 777}]}, {})
    assert await auto.fetch_tiers(s, 137940156) == {}
    assert len(s.urls) == 1


@pytest.mark.asyncio
async def test_synchronized_t12_without_skill_stored_unknown(tmp_path, monkeypatch):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, nickname TEXT, alliance TEXT)")
        db.execute("INSERT INTO users VALUES (137940156, 'FUTURE', '1501')")
    monkeypatch.setattr(quality, "DB_PATH", path)
    # quality.read_quality uses its DB_PATH default at definition time; pass through a wrapper.
    old_read, old_save = quality.read_quality, quality.save_quality
    monkeypatch.setattr(quality, "read_quality", lambda fids: old_read(fids, path))
    monkeypatch.setattr(quality, "save_quality", lambda aid,fids,changes,editor: old_save(aid,fids,changes,editor,path))
    session = FakeSession(
        {"results": [{"fid": 137940156, "uid": 33763165}]},
        {"fid": 137940156, "expedition_troops": [
            {"kind": "inf", "tier": 12, "star": 9},
            {"kind": "lan", "tier": 12, "star": 10},
        ]}
    )
    assert await auto.sync_tiers(session, 137940156, 1501) == 2
    record = old_read([137940156], path)[137940156]
    assert record["infantry"] == (12, None)
    assert record["lancer"] == (12, None)
    assert quality.format_tier(*record["infantry"]) == "T12 · Skill Unknown"
    assert record["marksman"] == (None, None)


def test_t12_unknown_migration_preserves_old_skill(tmp_path):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, nickname TEXT, alliance TEXT)")
        db.execute("INSERT INTO users VALUES (42, 'Chief', '1')")
        db.executescript("""
            CREATE TABLE member_troop_quality (
                fid INTEGER PRIMARY KEY,
                infantry_tier INTEGER, infantry_skill INTEGER,
                lancer_tier INTEGER, lancer_skill INTEGER,
                marksman_tier INTEGER, marksman_skill INTEGER,
                updated_at TEXT NOT NULL, updated_by INTEGER NOT NULL,
                CHECK(infantry_tier IS NULL OR infantry_tier IN (0, 10, 11, 12)),
                CHECK(lancer_tier IS NULL OR lancer_tier IN (0, 10, 11, 12)),
                CHECK(marksman_tier IS NULL OR marksman_tier IN (0, 10, 11, 12)),
                CHECK((infantry_tier=12 AND infantry_skill BETWEEN 0 AND 3)
                      OR (infantry_tier IS NOT 12 AND infantry_skill IS NULL)),
                CHECK((lancer_tier=12 AND lancer_skill BETWEEN 0 AND 3)
                      OR (lancer_tier IS NOT 12 AND lancer_skill IS NULL)),
                CHECK((marksman_tier=12 AND marksman_skill BETWEEN 0 AND 3)
                      OR (marksman_tier IS NOT 12 AND marksman_skill IS NULL))
            );
            INSERT INTO member_troop_quality VALUES
                (42, 12, 2, 11, NULL, NULL, NULL, '2026-11-15T00:00:00+00:00', 15);
        """)
    quality.initialize(path)
    assert quality.read_quality([42], path)[42]["infantry"] == (12, 2)
    assert quality.save_quality(1, [42], {"lancer": (12, None)}, 0, path) == 1
    assert quality.read_quality([42], path)[42]["lancer"] == (12, None)


class RefreshSession:
    """Website deep profile gets populated only after a queued refresh."""
    def __init__(self, *, accepted=True, missing=False):
        self.requests = []
        self.accepted = accepted
        self.missing = missing
        self.polls = 0

    def get(self, url, **kwargs):
        self.requests.append(("GET", url))
        if url.endswith("/api/search"):
            return FakeResponse({"results": [{"uid": 313, "fid": 123456789}]})
        if url.endswith("/refresh/status"):
            self.polls += 1
            return FakeResponse({"queued": self.polls <= 1, "started": False})
        if self.missing or self.polls == 0:
            return FakeResponse({"fid": 123456789, "expedition_troops": [],
                                 "expedition_troops_at": None})
        return FakeResponse({
            "fid": 123456789, "expedition_troops_at": 1794701128,
            "expedition_troops": [
                {"kind": "inf", "tier": 12, "star": 9, "count": 15},
                {"kind": "mm", "tier": 11, "star": 10, "count": 1},
            ],
        })

    def post(self, url, **kwargs):
        self.requests.append(("POST", url))
        assert kwargs["params"] == {"force": "0"}
        return FakeResponse({"accepted": self.accepted, "cached": not self.accepted})


@pytest.mark.asyncio
async def test_refresh_missing_profile_then_import(monkeypatch):
    monkeypatch.setattr(auto, "claim_refresh", lambda fid: True)

    async def no_delay(seconds):
        assert seconds == auto.REFRESH_POLL_SECONDS

    monkeypatch.setattr(auto.asyncio, "sleep", no_delay)
    session = RefreshSession()
    assert await auto.fetch_tiers(session, 123456789) == {
        "infantry": 12, "marksman": 11
    }
    assert len([x for x in session.requests if x[0] == "POST"]) == 1
    assert session.polls >= 1


@pytest.mark.asyncio
async def test_refresh_cooldown_prevents_post(monkeypatch):
    monkeypatch.setattr(auto, "claim_refresh", lambda fid: False)
    session = RefreshSession()
    assert await auto.fetch_tiers(session, 123456789) == {}
    assert not any(method == "POST" for method, _ in session.requests)


@pytest.mark.asyncio
async def test_refresh_denied_returns_unknown(monkeypatch):
    monkeypatch.setattr(auto, "claim_refresh", lambda fid: True)
    session = RefreshSession(accepted=False)
    assert await auto.fetch_tiers(session, 123456789) == {}
    assert sum(method == "POST" for method, _ in session.requests) == 1
    assert session.polls == 0


@pytest.mark.asyncio
async def test_refresh_completed_without_troops_stays_unknown(monkeypatch):
    monkeypatch.setattr(auto, "claim_refresh", lambda fid: True)
    monkeypatch.setattr(auto, "REFRESH_POLL_ATTEMPTS", 2)

    async def no_delay(seconds):
        return None

    monkeypatch.setattr(auto.asyncio, "sleep", no_delay)
    session = RefreshSession(missing=True)
    assert await auto.fetch_tiers(session, 123456789) == {}
    assert session.polls == 2


@pytest.mark.asyncio
async def test_existing_deep_data_does_not_refresh(monkeypatch):
    monkeypatch.setattr(auto, "claim_refresh", lambda fid: (_ for _ in ()).throw(
        AssertionError("should not refresh an already checked profile")))
    session = RefreshSession()
    session.polls = 1
    assert await auto.fetch_tiers(session, 123456789) == {
        "infantry": 12, "marksman": 11
    }


def test_refresh_claim_is_persistent_and_time_bounded(tmp_path):
    path = tmp_path / "refresh.sqlite"
    assert auto.claim_refresh(123, now=1000, path=path)
    assert not auto.claim_refresh(123, now=1001, path=path)
    assert not auto.claim_refresh(123, now=1599, path=path)
    assert auto.claim_refresh(123, now=1600, path=path)
    assert auto.claim_refresh(124, now=1601, path=path)


def test_pending_retry_survives_restart_and_keeps_original_due_time(tmp_path):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, alliance TEXT)")
        conn.executemany("INSERT INTO users (fid, alliance) VALUES (?, ?)",
                         [(101, "9"), (102, "9")])
    auto.pending_schedule(101, 9, now=1000, path=path)
    auto.pending_schedule(101, 9, now=1050, path=path)
    assert auto.pending_due(now=1059, path=path) == []
    assert auto.pending_due(now=1060, path=path) == [(101, 9)]
    assert auto.pending_due(now=1061, path=path) == []
    assert auto.pending_due(now=1180, path=path) == [(101, 9)]
    auto.pending_clear(101, path=path)
    assert auto.pending_due(now=9999, path=path) == []


def test_pending_retry_discards_removed_alliance_member(tmp_path):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, alliance TEXT)")
        conn.execute("INSERT INTO users VALUES (101, '10')")
    auto.pending_schedule(101, 9, now=0, path=path)
    assert auto.pending_due(now=100, path=path) == []
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM troop_quality_pending").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_no_tiers_queues_background_check(tmp_path, monkeypatch):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, alliance TEXT)")
        conn.execute("INSERT INTO users VALUES (123456789, '9')")
    monkeypatch.setattr(auto, "fetch_tiers", lambda session, fid: _no_tiers())
    old_pending = auto.pending_schedule
    monkeypatch.setattr(auto, "pending_schedule",
                        lambda fid, alliance_id: old_pending(fid, alliance_id, now=1000, path=path))
    assert await auto.sync_tiers(None, 123456789, 9) == 0
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT fid, alliance_id FROM troop_quality_pending").fetchone() == (123456789, 9)


async def _no_tiers():
    return {}


@pytest.mark.asyncio
async def test_worker_resolves_tiers_without_manual_sync(tmp_path, monkeypatch):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, nickname TEXT, alliance TEXT)")
        db.execute("INSERT INTO users VALUES (123456789, 'Cake', '9')")
    auto.pending_schedule(123456789, 9, now=0, path=path)
    original_due = auto.pending_due
    monkeypatch.setattr(auto, "pending_due", lambda limit=3: original_due(now=100, limit=limit, path=path))
    old_read, old_save = quality.read_quality, quality.save_quality
    monkeypatch.setattr(quality, "read_quality", lambda fids: old_read(fids, path))
    monkeypatch.setattr(quality, "save_quality", lambda aid, fids, changes, editor: old_save(aid, fids, changes, editor, path))
    original_clear = auto.pending_clear
    monkeypatch.setattr(auto, "pending_clear", lambda fid: original_clear(fid, path=path))
    async def tiers(session, fid):
        return {"infantry": 11, "lancer": 11, "marksman": 10}
    monkeypatch.setattr(auto, "sync_official_pending",
                        lambda session, fid, alliance_id:
                        auto.sync_tiers(session, fid, alliance_id, detected={
                            "infantry": 11, "lancer": 11, "marksman": 10
                        }))
    assert await auto.process_pending(None) == 3
    assert old_read([123456789], path)[123456789] == {
        "infantry": (11, None), "lancer": (11, None), "marksman": (10, None)
    }
    assert original_due(now=999999, path=path) == []


def auto_pending(path):
    return auto.pending_due(now=100, path=path)


def clear_pending(fid, path):
    return auto.pending_clear(fid, path=path)


class LiveSearchSession:
    def __init__(self, *, exact=True, indexed=False):
        self.requests = []
        self.exact, self.indexed = exact, indexed

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs.get("params")))
        if url.endswith("/api/search"):
            live = kwargs["params"]["live"]
            if live == "0" and not self.indexed:
                return FakeResponse({"results": []})
            return FakeResponse({"results": [{
                "fid": 123456789 if self.exact else 123456780, "uid": 456
            }]})
        return FakeResponse({
            "fid": 123456789,
            "expedition_troops": [
                {"kind": "inf", "tier": 11, "star": 10},
                {"kind": "lan", "tier": 10, "star": 9},
                {"kind": "mm", "tier": 12, "star": 10}
            ],
        })


@pytest.mark.asyncio
async def test_state_scoped_deep_search_recovers_unindexed_member(monkeypatch):
    monkeypatch.setattr(auto, "member_state", lambda fid: 920)
    monkeypatch.setattr(auto, "claim_deep_search", lambda fid: True)
    s = LiveSearchSession()
    assert await auto.fetch_tiers(s, 123456789) == {
        "infantry": 11, "lancer": 10, "marksman": 12
    }
    assert s.requests[0][1] == {"q": "123456789", "limit": "10", "live": "0"}
    assert s.requests[1][1] == {
        "q": "123456789", "limit": "40", "live": "1", "kid": "920"
    }
    assert s.requests[2][0].endswith("/api/players/456")


@pytest.mark.asyncio
async def test_deep_search_never_uses_mismatched_fid(monkeypatch):
    monkeypatch.setattr(auto, "member_state", lambda fid: 920)
    monkeypatch.setattr(auto, "claim_deep_search", lambda fid: True)
    s = LiveSearchSession(exact=False)
    assert await auto.fetch_tiers(s, 123456789) == {}
    assert len(s.requests) == 2


@pytest.mark.asyncio
async def test_deep_search_skipped_without_known_state(monkeypatch):
    monkeypatch.setattr(auto, "member_state", lambda fid: None)
    monkeypatch.setattr(auto, "claim_deep_search", lambda fid: (_ for _ in ()).throw(
        AssertionError("cannot search live globally")))
    s = LiveSearchSession()
    assert await auto.fetch_tiers(s, 123456789) == {}
    assert len(s.requests) == 1


@pytest.mark.asyncio
async def test_deep_search_skipped_on_cooldown(monkeypatch):
    monkeypatch.setattr(auto, "member_state", lambda fid: 920)
    monkeypatch.setattr(auto, "claim_deep_search", lambda fid: False)
    s = LiveSearchSession()
    assert await auto.fetch_tiers(s, 123456789) == {}
    assert len(s.requests) == 1


@pytest.mark.asyncio
async def test_indexed_member_never_invokes_deep_search(monkeypatch):
    monkeypatch.setattr(auto, "member_state", lambda fid: (_ for _ in ()).throw(
        AssertionError("no deep search for indexed member")))
    s = LiveSearchSession(indexed=True)
    assert await auto.fetch_tiers(s, 123456789) == {
        "infantry": 11, "lancer": 10, "marksman": 12
    }
    assert len(s.requests) == 2


def test_deep_search_cooldown_persists(tmp_path):
    path = tmp_path / "users.sqlite"
    assert auto.claim_deep_search(22, now=1000, path=path)
    assert not auto.claim_deep_search(22, now=1001, path=path)
    assert not auto.claim_deep_search(22, now=1000 + 6*3600 - 1, path=path)
    assert auto.claim_deep_search(22, now=1000 + 6*3600, path=path)


def test_member_state_comes_from_stored_player_not_alliance(tmp_path):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, alliance TEXT, kid INTEGER)")
        db.execute("INSERT INTO users VALUES (123456789, '1501', 920)")
    assert auto.member_state(123456789, path=path) == 920
    assert auto.member_state(999, path=path) is None


def test_troop_progress_reports_complete_partial_unknown_and_pending(tmp_path):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, nickname TEXT, alliance TEXT)")
        db.executemany("INSERT INTO users VALUES (?, ?, ?)", [
            (101, "Complete", "9"), (102, "Partial", "9"), (103, "Waiting", "9")
        ])
    quality.initialize(path)
    quality.save_quality(9, [101], {
        "infantry": (11, None), "lancer": (11, None), "marksman": (10, None)
    }, 0, path)
    quality.save_quality(9, [102], {"infantry": (12, None)}, 0, path)
    auto.pending_schedule(102, 9, now=0, path=path)
    auto.pending_schedule(103, 9, now=0, path=path)
    summary = auto.troop_progress(9, path=path)
    assert summary["9"] == {
        "total": 3, "complete": 1, "partial": 1, "unknown": 1, "pending": 2
    }


def test_troop_progress_before_tables_exist(tmp_path):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, alliance TEXT)")
        db.execute("INSERT INTO users VALUES (101, '9')")
    assert auto.troop_progress(9, path=path)["9"]["unknown"] == 1


@pytest.mark.asyncio
async def test_refresh_post_uses_same_origin_headers():
    class CapturingSession:
        def __init__(self):
            self.headers = None
        def post(self, url, **kwargs):
            self.headers = kwargs["headers"]
            return FakeResponse({"accepted": False})
    s = CapturingSession()
    await auto._json(s, "/api/players/33747502/refresh",
                     params={"force": "0"}, method="POST")
    assert s.headers["Origin"] == "https://whiteoutstats.com"
    assert s.headers["Referer"] == "https://whiteoutstats.com/player/33747502"
    assert "Cookie" not in s.headers


def test_refresh_denial_backoff(tmp_path):
    path = tmp_path / "users.sqlite"
    assert not auto.refresh_denied_active(12, now=1000, path=path)
    auto.mark_refresh_denied(12, now=1000, path=path)
    assert auto.refresh_denied_active(12, now=1001, path=path)
    assert not auto.refresh_denied_active(
        12, now=1000 + auto.REFRESH_DENIED_SECONDS, path=path
    )


def test_official_troops_section_parses_without_fc_or_skills():
    payload = {
        "ok": True,
        "player": {"chief_id": 134958026},
        "troops": [
            {"kind": "inf", "tier": 12, "star": 10, "count": 3},
            {"kind": "lan", "tier": 11, "star": 10, "count": 8},
            {"kind": "mm", "tier": 12, "star": 10, "count": 2},
        ],
    }
    assert auto.official_tiers(payload) == {
        "infantry": 12, "lancer": 11, "marksman": 12
    }
    assert auto.official_tiers({"player": {"troops": {
        "infantry": {"tier": 11}, "lancer": {"tier": 10},
        "marksman": {"tier": 12, "star": 9}
    }}}) == {"infantry": 11, "lancer": 10, "marksman": 12}
    assert auto.official_tiers({"player": {"furnace_level": 80}}) == {}


@pytest.mark.asyncio
async def test_official_sync_never_touches_website(tmp_path, monkeypatch):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, nickname TEXT, alliance TEXT)")
        db.execute("INSERT INTO users VALUES (134958026, 'Cori_Y', '9')")
    old_read, old_save = quality.read_quality, quality.save_quality
    monkeypatch.setattr(quality, "read_quality", lambda fids: old_read(fids, path))
    monkeypatch.setattr(quality, "save_quality",
                        lambda aid, fids, changes, editor:
                        old_save(aid, fids, changes, editor, path))
    monkeypatch.setattr(auto, "fetch_tiers", lambda *args:
                        (_ for _ in ()).throw(AssertionError("website must not be contacted")))
    original_clear = auto.pending_clear
    monkeypatch.setattr(auto, "pending_clear", lambda fid: original_clear(fid, path=path))
    assert await auto.sync_tiers(None, 134958026, 9, detected={
        "infantry": 12, "lancer": 11, "marksman": 12
    }) == 3
    assert old_read([134958026], path)[134958026]["lancer"] == (11, None)


def test_official_retry_schedule_uses_api_retry_after(tmp_path):
    path = tmp_path / "users.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (fid INTEGER PRIMARY KEY, alliance TEXT)")
        db.execute("INSERT INTO users VALUES (77, '9')")
    auto.schedule_official_retry(77, 9, 45, now=1000, path=path)
    assert auto.pending_due(now=1044, path=path) == []
    assert auto.pending_due(now=1045, path=path) == [(77, 9)]
