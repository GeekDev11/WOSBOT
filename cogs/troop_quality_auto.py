"""Best-effort troop tier import from WhiteoutStats' public website player profile.

The website uses a different UID from the in-game Chief ID (FID). Query the
website search route for an exact FID match, then read expedition_troops from
/api/players/{uid}. Never infer T12 skill level from star (FC).
"""
import asyncio
import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import aiohttp
from . import troop_quality as quality

logger = logging.getLogger("alliance")
BASE = "https://whiteoutstats.com"
TIMEOUT = aiohttp.ClientTimeout(total=15)
KINDS = {"inf": "infantry", "lan": "lancer", "mm": "marksman"}
VALID_TIERS = {10, 11, 12}


def extract_tiers(profile):
    """Highest explicitly reported tier for each type, ignoring count and FC/star."""
    if not isinstance(profile, dict):
        return {}
    records = profile.get("expedition_troops")
    if not isinstance(records, list):
        return {}
    result = {}
    for row in records:
        if not isinstance(row, dict):
            continue
        kind = KINDS.get(row.get("kind"))
        tier = row.get("tier")
        if kind is None or type(tier) not in (int, float):
            continue
        if int(tier) != tier or int(tier) not in VALID_TIERS:
            continue
        result[kind] = max(result.get(kind, 0), int(tier))
    return result


def merge_tiers(detected, existing):
    """Update known tiers; retain manually recorded T12 skills and avoid downgrades.

    T12 with no skill recorded stays NULL/Unknown until explicitly selected.
    """
    changes = {}
    for troop, tier in detected.items():
        prior_tier, prior_skill = existing.get(troop, (None, None))
        if prior_tier is not None and prior_tier > tier:
            continue  # a delayed website cache must not downgrade a known troop
        skill = prior_skill if tier == 12 and prior_tier == 12 else None
        if (tier, skill) != (prior_tier, prior_skill):
            changes[troop] = (tier, skill)
    return changes


# The website profile uses a separate refresh queue; the button has a ~10-minute
# cooldown. Persist the cooldown in SQLite across restarts and concurrent jobs.
REFRESH_COOLDOWN_SECONDS = 600
REFRESH_DENIED_SECONDS = 6 * 3600  # Avoid repeated denied POST requests
REFRESH_POLL_SECONDS = 2.0
REFRESH_POLL_ATTEMPTS = 6
REFRESH_DB = Path("db/users.sqlite")


def claim_refresh(fid, now=None, path=REFRESH_DB):
    """Atomically claim a non-forced refresh for one chief, at most once/10 min."""
    now = time.time() if now is None else now
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("""
            CREATE TABLE IF NOT EXISTS troop_quality_refresh_attempts (
                fid INTEGER PRIMARY KEY,
                attempted_at REAL NOT NULL
            )
        """)
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute(
                "SELECT attempted_at FROM troop_quality_refresh_attempts WHERE fid=?",
                (int(fid),),
            ).fetchone()
            if row is not None and now - float(row[0]) < REFRESH_COOLDOWN_SECONDS:
                db.commit()
                return False
            db.execute(
                "INSERT INTO troop_quality_refresh_attempts (fid, attempted_at) "
                "VALUES (?, ?) ON CONFLICT(fid) DO UPDATE SET attempted_at=excluded.attempted_at",
                (int(fid), float(now)),
            )
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise


def mark_refresh_denied(fid, *, now=None, path=REFRESH_DB):
    """Persist a suppression interval when the website explicitly refuses refresh."""
    now = time.time() if now is None else float(now)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS troop_quality_refresh_denied (
                fid INTEGER PRIMARY KEY, denied_at REAL NOT NULL
            )
        """)
        db.execute(
            "INSERT INTO troop_quality_refresh_denied (fid, denied_at) VALUES (?, ?) "
            "ON CONFLICT(fid) DO UPDATE SET denied_at=excluded.denied_at",
            (int(fid), now),
        )


def refresh_denied_active(fid, *, now=None, path=REFRESH_DB):
    """Denials suppress POSTs, never public cached-profile reads."""
    now = time.time() if now is None else float(now)
    try:
        with closing(sqlite3.connect(path, timeout=30)) as db:
            row = db.execute(
                "SELECT denied_at FROM troop_quality_refresh_denied WHERE fid=?",
                (int(fid),),
            ).fetchone()
        return bool(row and now - float(row[0]) < REFRESH_DENIED_SECONDS)
    except sqlite3.OperationalError:
        return False


# Deep website searches scan live state data; keep them rare and state-scoped.
DEEP_SEARCH_COOLDOWN_SECONDS = 6 * 3600


def claim_deep_search(fid, now=None, path=REFRESH_DB):
    """Atomically limit expensive live searches to one per chief every six hours."""
    now = time.time() if now is None else float(now)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("""
            CREATE TABLE IF NOT EXISTS troop_quality_deep_search_attempts (
                fid INTEGER PRIMARY KEY,
                attempted_at REAL NOT NULL
            )
        """)
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute(
                "SELECT attempted_at FROM troop_quality_deep_search_attempts WHERE fid=?",
                (int(fid),),
            ).fetchone()
            if row is not None and now - float(row[0]) < DEEP_SEARCH_COOLDOWN_SECONDS:
                db.commit()
                return False
            db.execute(
                "INSERT INTO troop_quality_deep_search_attempts (fid, attempted_at) "
                "VALUES (?, ?) ON CONFLICT(fid) DO UPDATE SET attempted_at=excluded.attempted_at",
                (int(fid), now),
            )
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise


def member_state(fid, path=REFRESH_DB):
    """Return the known game state for scoped live lookup; never guess one."""
    try:
        with closing(sqlite3.connect(path, timeout=30)) as db:
            row = db.execute("SELECT kid FROM users WHERE fid=?", (int(fid),)).fetchone()
    except sqlite3.Error as exc:
        logger.info("Troop Quality: member state unavailable for %s: %s", fid, exc)
        return None
    if not row or row[0] is None:
        return None
    try:
        kid = int(row[0])
    except (ValueError, TypeError):
        return None
    return kid if kid > 0 else None


def exact_website_match(search, fid):
    """Ignore partial-name matches and similarly numbered Chief IDs."""
    if not isinstance(search, dict):
        return None
    for row in search.get("results") or ():
        if (isinstance(row, dict) and str(row.get("fid")) == str(fid)
                and type(row.get("uid")) is int and row["uid"] > 0):
            return row
    return None


async def _json(session, path, *, params=None, method="GET"):
    headers = {"Accept": "application/json"}
    if method == "POST":
        # Use the website's normal same-origin context, without client cookies.
        uid = path.split("/")[3] if path.startswith("/api/players/") else None
        headers.update({
            "Origin": BASE,
            "Referer": f"{BASE}/player/{uid}" if uid else BASE + "/",
        })
    kwargs = {"headers": headers, "timeout": TIMEOUT}
    if params is not None:
        kwargs["params"] = params
    call = session.post if method == "POST" else session.get
    async with call(BASE + path, **kwargs) as response:
        if response.status not in (200, 202):
            logger.info("Troop Quality: website %s returned HTTP %s", path, response.status)
            if response.status == 403 and method == "POST":
                return {"_refresh_forbidden": True}
            return None
        return await response.json(content_type=None)


async def _refresh_missing(session, uid, fid, initial_profile, *, sleep=asyncio.sleep):
    """Request only a permitted refresh, then briefly inspect queue and cached profile.

    Do not force-refresh, bypass cooldowns, or keep polling indefinitely.
    A worker that takes longer can be picked up on the next Player Sync.
    """
    if await asyncio.to_thread(refresh_denied_active, fid):
        logger.info("Troop Quality: website refresh denied earlier for Chief ID %s; "
                    "checking cached profile only", fid)
        return {}
    if not await asyncio.to_thread(claim_refresh, fid):
        logger.info("Troop Quality: refresh cooldown active for Chief ID %s", fid)
        return {}
    path = f"/api/players/{uid}"
    logger.info("Troop Quality: requesting non-forced detailed profile refresh for Chief ID %s", fid)
    refresh = await _json(session, path + "/refresh", params={"force": "0"}, method="POST")
    if not isinstance(refresh, dict):
        return {}
    if refresh.get("_refresh_forbidden"):
        await asyncio.to_thread(mark_refresh_denied, fid)
        logger.info("Troop Quality: refresh forbidden for Chief ID %s; "
                    "suppressing refresh POSTs for six hours", fid)
        return {}
    player = refresh.get("player")
    if isinstance(player, dict) and (not player.get("fid") or str(player["fid"]) == str(fid)):
        direct = extract_tiers(player)
        if direct:
            return direct
    if not refresh.get("accepted"):
        logger.info("Troop Quality: website did not queue a refresh for Chief ID %s (cached=%s)",
                    fid, bool(refresh.get("cached")))
        return {}
    stamp = initial_profile.get("expedition_troops_at")
    for attempt in range(REFRESH_POLL_ATTEMPTS):
        await sleep(REFRESH_POLL_SECONDS)
        status = await _json(session, path + "/refresh/status")
        if not isinstance(status, dict):
            break
        if status.get("queued") or status.get("started"):
            # The worker can finish before status is updated. Re-read periodically.
            if attempt not in (2, 5):
                continue
        updated = await _json(session, path)
        if not isinstance(updated, dict):
            break
        if updated.get("fid") is not None and str(updated["fid"]) != str(fid):
            logger.warning("Troop Quality: refreshed profile UID/FID mismatch for %s", fid)
            return {}
        if updated.get("expedition_troops_at") != stamp or not (status.get("queued") or status.get("started")):
            found = extract_tiers(updated)
            if found:
                logger.info("Troop Quality: detailed refresh imported %s tier(s) for Chief ID %s",
                            len(found), fid)
                return found
            if not (status.get("queued") or status.get("started")):
                break
    logger.info("Troop Quality: detailed refresh still unavailable for Chief ID %s; keeping Unknown", fid)
    return {}


async def fetch_tiers(session, fid):
    """Resolve FID -> website UID and fetch tiers; refresh missing deep data safely."""
    fid = int(fid)
    search = await _json(
        session, "/api/search",
        params={"q": str(fid), "limit": "10", "live": "0"}
    )
    if not isinstance(search, dict):
        logger.info("Troop Quality: website search unavailable for Chief ID %s", fid)
        return {}
    match = exact_website_match(search, fid)
    if match is None:
        # Mirror the website's optional deep search, but scope it to the
        # member's known state and enforce a durable per-chief cooldown.
        kid = await asyncio.to_thread(member_state, fid)
        if kid is None:
            logger.info("Troop Quality: no indexed website UID for %s; cannot live-search without known state", fid)
            return {}
        if not await asyncio.to_thread(claim_deep_search, fid):
            logger.info("Troop Quality: live-search cooldown active for Chief ID %s", fid)
            return {}
        logger.info("Troop Quality: trying state-scoped live search for Chief ID %s in state %s", fid, kid)
        deep = await _json(
            session, "/api/search",
            params={"q": str(fid), "limit": "40", "live": "1", "kid": str(kid)},
        )
        match = exact_website_match(deep, fid)
        if match is None:
            logger.info("Troop Quality: no exact website UID match for Chief ID %s after live search", fid)
            return {}
    uid = match["uid"]
    profile = await _json(session, f"/api/players/{uid}")
    if not isinstance(profile, dict):
        logger.info("Troop Quality: no website profile for Chief ID %s", fid)
        return {}
    if profile.get("fid") is not None and str(profile["fid"]) != str(fid):
        logger.warning("Troop Quality: mismatched website profile for Chief ID %s", fid)
        return {}
    found = extract_tiers(profile)
    if found:
        return found
    # A recorded deep read with zero supported tiers is not permission to
    # overwrite records or keep triggering the website refresh queue.
    if profile.get("expedition_troops_at") is not None:
        logger.info("Troop Quality: detailed profile has no T10–T12 records for Chief ID %s", fid)
        return {}
    return await _refresh_missing(session, uid, fid, profile)


# Official authenticated player response support. Website UID/search/refresh
# functions above are legacy only and are not called by production Player Sync.
def official_tiers(payload):
    """Extract only explicit troop tiers; never treat FC star/count as skill."""
    if not isinstance(payload, dict):
        return {}
    player = payload.get("player")
    if not isinstance(player, dict):
        player = {}
    # The official API may expose troop rows as a named section or nested
    # in player. Only accept explicit tier fields and recognized troop kinds.
    for source in (payload, player):
        for name in ("troops", "expedition_troops"):
            records = source.get(name)
            if isinstance(records, dict):
                records = records.get("expedition_troops", records.get("items", records))
                if isinstance(records, dict):
                    records = [
                        dict(row, kind=kind) for kind, row in records.items()
                        if isinstance(row, dict)
                    ]
            if not isinstance(records, list):
                continue
            normalized = []
            kind_aliases = {"infantry": "inf", "lancer": "lan",
                            "marksman": "mm", "inf": "inf", "lan": "lan", "mm": "mm"}
            for row in records:
                if not isinstance(row, dict):
                    continue
                kind = kind_aliases.get(str(row.get("kind", row.get("type", ""))).lower())
                if kind is None:
                    continue
                normalized.append({"kind": kind, "tier": row.get("tier")})
            result = extract_tiers({"expedition_troops": normalized})
            if result:
                return result
    return {}


async def sync_official_pending(session, fid, alliance_id):
    """Refresh missing tiers through the configured, authenticated public API."""
    from . import alliance_api_sync as api_sync
    from .chief_stalker import with_include
    profile, key = await asyncio.to_thread(api_sync.load_connection)
    if not key or api_sync.missing_for_sync(profile):
        logger.info("Troop Quality: official API connection unavailable for %s", fid)
        return 0
    if not await api_sync._take_slot(profile):
        logger.info("Troop Quality: official API quota exhausted; deferring %s", fid)
        return 0
    rich = with_include(profile, ("base", "troops"))
    result = await api_sync.fetch_player(session, rich, key, fid)
    if result.status == 200:
        return await sync_tiers(session, fid, alliance_id,
                                detected=official_tiers(result.payload),
                                stale=result.payload if isinstance(result.payload, dict) else None)
    logger.info("Troop Quality: official troop fetch returned HTTP %s for %s",
                result.status, fid)
    return 0


# Durable deferred checks: a website refresh may complete long after Player Sync.
# Retain the FID/alliance pair so the running bot can revisit it without a user
# opening a web page or launching another manual sync.
PENDING_INTERVALS = (60, 120, 300, 600, 1200, 3600)


def _pending_db(path=REFRESH_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("""
        CREATE TABLE IF NOT EXISTS troop_quality_pending (
            fid INTEGER PRIMARY KEY,
            alliance_id INTEGER NOT NULL,
            next_check_at REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_reason TEXT
        )
    """)
    db.commit()
    return db


def pending_schedule(fid, alliance_id, *, now=None, path=REFRESH_DB):
    """Queue without delaying an existing retry; survives restarts."""
    now = time.time() if now is None else float(now)
    with closing(_pending_db(path)) as db, db:
        db.execute(
            "INSERT INTO troop_quality_pending "
            "(fid, alliance_id, next_check_at, attempts, last_reason) "
            "VALUES (?, ?, ?, 0, ?) ON CONFLICT(fid) DO UPDATE SET "
            "alliance_id=excluded.alliance_id",
            (int(fid), int(alliance_id), now + PENDING_INTERVALS[0], "awaiting detail"),
        )


def schedule_official_retry(fid, alliance_id, retry_after, *, now=None, path=REFRESH_DB):
    """Honor API retry_after for stale player responses, without busy polling."""
    try:
        delay = float(retry_after)
    except (TypeError, ValueError):
        delay = 60.0
    if not (0 < delay <= 86400):
        delay = 60.0
    now = time.time() if now is None else float(now)
    with closing(_pending_db(path)) as db, db:
        db.execute(
            "INSERT INTO troop_quality_pending "
            "(fid, alliance_id, next_check_at, attempts, last_reason) "
            "VALUES (?, ?, ?, 0, 'official API stale') "
            "ON CONFLICT(fid) DO UPDATE SET alliance_id=excluded.alliance_id, "
            "next_check_at=excluded.next_check_at, last_reason='official API stale'",
            (int(fid), int(alliance_id), now + delay),
        )


def pending_clear(fid, *, path=REFRESH_DB):
    with closing(_pending_db(path)) as db, db:
        db.execute("DELETE FROM troop_quality_pending WHERE fid=?", (int(fid),))


def pending_due(*, now=None, limit=3, path=REFRESH_DB):
    """Reserve a small due batch atomically to avoid duplicate processing."""
    now = time.time() if now is None else float(now)
    with closing(_pending_db(path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            rows = db.execute(
                "SELECT p.fid, p.alliance_id, p.attempts FROM troop_quality_pending p "
                "JOIN users u ON u.fid=p.fid AND u.alliance=CAST(p.alliance_id AS TEXT) "
                "WHERE p.next_check_at <= ? ORDER BY p.next_check_at LIMIT ?",
                (now, limit),
            ).fetchall()
            for fid, alliance_id, attempt in rows:
                delay = PENDING_INTERVALS[min(attempt + 1, len(PENDING_INTERVALS) - 1)]
                db.execute(
                    "UPDATE troop_quality_pending "
                    "SET attempts=?, next_check_at=?, last_reason='waiting for website' "
                    "WHERE fid=?",
                    (attempt + 1, now + delay, fid),
                )
            # Discard rows for members no longer registered in this alliance.
            db.execute(
                "DELETE FROM troop_quality_pending WHERE NOT EXISTS "
                "(SELECT 1 FROM users u WHERE u.fid=troop_quality_pending.fid "
                "AND u.alliance=CAST(troop_quality_pending.alliance_id AS TEXT))"
            )
            db.commit()
            return [(int(fid), int(alliance_id)) for fid, alliance_id, _ in rows]
        except Exception:
            db.rollback()
            raise


def troop_progress(alliance_id=None, path=REFRESH_DB):
    """Read-only member coverage for Sync Log; a green API sync is not troop success."""
    path = Path(path)
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.row_factory = sqlite3.Row
        tables = {row["name"] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "users" not in tables:
            return {}
        has_tiers = "member_troop_quality" in tables
        has_pending = "troop_quality_pending" in tables
        where = "WHERE u.alliance=? " if alliance_id is not None else "WHERE u.alliance IS NOT NULL AND u.alliance!='' "
        params = (str(alliance_id),) if alliance_id is not None else ()
        join = ("LEFT JOIN member_troop_quality t ON t.fid=u.fid " if has_tiers else "")
        join += ("LEFT JOIN troop_quality_pending p ON p.fid=u.fid " if has_pending else "")
        cols = ("t.infantry_tier,t.lancer_tier,t.marksman_tier" if has_tiers
                else "NULL AS infantry_tier,NULL AS lancer_tier,NULL AS marksman_tier")
        pending_col = "p.fid AS pending_fid" if has_pending else "NULL AS pending_fid"
        rows = db.execute(
            f"SELECT u.alliance, {cols}, {pending_col} FROM users u {join} {where}",
            params,
        ).fetchall()
    stats = {}
    for row in rows:
        alliance = str(row["alliance"])
        info = stats.setdefault(alliance, {"total": 0, "complete": 0, "partial": 0,
                                           "unknown": 0, "pending": 0})
        info["total"] += 1
        count = sum(row[k] in VALID_TIERS for k in ("infantry_tier", "lancer_tier", "marksman_tier"))
        if count == 3:
            info["complete"] += 1
        elif count:
            info["partial"] += 1
        else:
            info["unknown"] += 1
        if row["pending_fid"] is not None:
            info["pending"] += 1
    return stats


async def process_pending(session, *, limit=3):
    """Run a bounded background batch, independent of normal Player Sync."""
    rows = await asyncio.to_thread(pending_due, limit=limit)
    saved = 0
    for fid, alliance_id in rows:
        saved += await sync_official_pending(session, fid, alliance_id)
    return saved


async def sync_tiers(session, fid, alliance_id, *, detected=None, stale=None):
    """Write explicit tiers; official payload skips legacy website requests."""
    try:
        tiers = await fetch_tiers(session, fid) if detected is None else detected
        if not tiers:
            if isinstance(stale, dict):
                player = stale.get("player") if isinstance(stale.get("player"), dict) else {}
                logger.info("Troop Quality: official API returned no tiers for %s "
                            "(fresh=%s, checked_at=%s, top_troops=%s, player_troops=%s)",
                            fid, stale.get("fresh"),
                            stale.get("troops_checked_at", player.get("troops_checked_at")),
                            type(stale.get("troops")).__name__,
                            type(player.get("troops")).__name__)
            if isinstance(stale, dict) and stale.get("fresh") is False:
                await asyncio.to_thread(schedule_official_retry, fid, alliance_id,
                                        stale.get("retry_after"))
            else:
                await asyncio.to_thread(pending_schedule, fid, alliance_id)
            return 0
        existing = await asyncio.to_thread(quality.read_quality, [int(fid)])
        changes = merge_tiers(tiers, existing.get(int(fid), {}))
        changed = 0
        if changes:
            # System editor 0 is distinct from human Discord administrators.
            changed = await asyncio.to_thread(
                quality.save_quality, int(alliance_id), [int(fid)], changes, 0
            )
        # A partially populated website result should be revisited; never
        # manufacture unknown troop types. Drop the task only when all types
        # were returned and the database update has succeeded.
        if len(tiers) < len(quality.TROOPS):
            if isinstance(stale, dict) and stale.get("fresh") is False:
                await asyncio.to_thread(schedule_official_retry, fid, alliance_id,
                                        stale.get("retry_after"))
            else:
                await asyncio.to_thread(pending_schedule, fid, alliance_id)
        else:
            await asyncio.to_thread(pending_clear, fid)
        return changed
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError) as exc:
        logger.info("Troop Quality: no website tier update for %s: %s", fid, exc)
    except Exception:
        logger.exception("Troop Quality: unexpected tier import failure for %s", fid)
    return 0
