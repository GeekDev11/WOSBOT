"""Best-effort troop tier import from WhiteoutStats' public website player profile.

The website uses a different UID from the in-game Chief ID (FID). Query the
website search route for an exact FID match, then read expedition_troops from
/api/players/{uid}. Never infer T12 skill level from star (FC).
"""
import asyncio
import logging

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


async def _json(session, path, *, params=None):
    async with session.get(
        BASE + path,
        params=params,
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
    ) as response:
        if response.status != 200:
            logger.info("Troop Quality: website %s returned HTTP %s", path, response.status)
            return None
        return await response.json(content_type=None)


async def fetch_tiers(session, fid):
    """Return detected tier mapping or {}, never mark unavailable data as T10."""
    fid = int(fid)
    search = await _json(
        session, "/api/search",
        params={"q": str(fid), "limit": "10", "live": "0"}
    )
    if not isinstance(search, dict):
        return {}
    records = search.get("results") or []
    match = next(
        (r for r in records if isinstance(r, dict)
         and str(r.get("fid")) == str(fid) and r.get("uid") is not None), None
    )
    if match is None:
        return {}
    uid = match["uid"]
    if type(uid) is not int or uid <= 0:
        return {}
    profile = await _json(session, f"/api/players/{uid}")
    if not isinstance(profile, dict):
        return {}
    # Guard against a mismatched or stale website UID.
    if str(profile.get("fid", fid)) != str(fid):
        return {}
    return extract_tiers(profile)


async def sync_tiers(session, fid, alliance_id):
    """Best-effort write; never blocks ordinary member sync on website failure."""
    try:
        tiers = await fetch_tiers(session, fid)
        if not tiers:
            return 0
        existing = await asyncio.to_thread(quality.read_quality, [int(fid)])
        changes = merge_tiers(tiers, existing.get(int(fid), {}))
        if not changes:
            return 0
        # 0 is the system editor ID, distinct from real Discord administrators.
        return await asyncio.to_thread(
            quality.save_quality, int(alliance_id), [int(fid)], changes, 0
        )
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError) as exc:
        logger.info("Troop Quality: no website tier update for %s: %s", fid, exc)
    except Exception:
        logger.exception("Troop Quality: unexpected tier import failure for %s", fid)
    return 0
