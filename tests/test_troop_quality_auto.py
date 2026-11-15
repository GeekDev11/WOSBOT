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
