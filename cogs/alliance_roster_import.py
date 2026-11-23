"""Safe roster discovery and additive import from WhiteoutStats.

Only use for verified, guild-bound managed alliances. API observations never
reassign existing chief IDs or automatically remove members.
"""
import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import aiohttp

from . import alliance_api_sync as sync

USERS_DB = "db/users.sqlite"
ALLIANCE_DB = "db/alliance.sqlite"


@dataclass(frozen=True)
class RosterResult:
    received: int
    added: int
    already_home: int
    conflicts: int
    invalid: int


def parse_roster(payload, resolved):
    if not isinstance(payload, dict) or payload.get("ok") is False:
        raise ValueError("WhiteoutStats did not confirm the roster.")
    if payload.get("fresh") is False:
        raise ValueError("Alliance roster is still refreshing; retry when it is fresh.")
    identity = payload.get("alliance")
    if not isinstance(identity, dict) or (
        str(identity.get("aid")) != str(resolved.aid)
        or str(identity.get("kid")) != str(resolved.state_id)
        or identity.get("abbr") != resolved.tag
    ):
        raise ValueError("Alliance identity changed during roster lookup.")
    members = payload.get("members")
    if not isinstance(members, list):
        raise ValueError("The API returned no roster list; import cancelled.")
    count = identity.get("count")
    if count is not None:
        try:
            if int(count) != len(members):
                raise ValueError("The API roster is incomplete; import cancelled.")
        except (TypeError, ValueError) as exc:
            raise ValueError("The API roster count could not be verified.") from exc
    entries = []
    seen = set()
    invalid = 0
    for member in members:
        if not isinstance(member, dict):
            invalid += 1
            continue
        raw_id = member.get("chief_id") or member.get("fid")
        if type(raw_id) not in (int, str) or not str(raw_id).isdigit() or int(raw_id) <= 0:
            invalid += 1
            continue
        fid = int(raw_id)
        if fid in seen:
            invalid += 1
            continue
        seen.add(fid)
        state = member.get("kid")
        if state is not None and str(state) != str(resolved.state_id):
            invalid += 1
            continue
        name = member.get("nick_name")
        if not isinstance(name, str) or not name.strip():
            invalid += 1
            continue
        def number(key):
            value = member.get(key)
            return int(value) if type(value) in (str, int) and str(value).isdigit() else None
        entries.append((fid, name.strip()[:100], number("furnace_level"),
                        resolved.state_id, number("power")))
    # Do not silently accept a partial roster due to malformed members.
    if invalid:
        raise ValueError(f"{invalid} roster entries were invalid; import cancelled.")
    return entries


async def fetch_roster(bot, resolved):
    cog = bot.get_cog("AllianceApiSync")
    if cog is None:
        raise ValueError("Player Sync is not loaded.")
    profile, key = await asyncio.to_thread(sync.load_connection)
    base = urlsplit(profile.get("base_url") or "")
    if base.scheme != "https" or base.hostname != "api.whiteoutstats.com" or not key:
        raise ValueError("Set up the WhiteoutStats API key in Player Sync first.")
    if not await sync._take_slot(profile):
        raise ValueError("Player Sync's API request budget is exhausted.")
    header = profile.get("auth_header", "X-Api-Key")
    credential = ("Bearer " + key if header.lower() == "authorization"
                  and not key.lower().startswith("bearer ") else key)
    url = ("https://api.whiteoutstats.com/v1/alliances/"
           + str(resolved.state_id) + "/" + quote(resolved.tag, safe=""))
    try:
        async with cog.session().get(
            url, params={"include": "info,roster", "wait": 30},
            headers={header: credential, "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=75),
        ) as response:
            if response.status != 200:
                raise ValueError(f"Alliance roster API returned HTTP {response.status}.")
            payload = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise ValueError(f"Unable to retrieve alliance roster: {exc}") from exc
    return parse_roster(payload, resolved)


def import_roster(guild_id, alliance_id, resolved, entries):
    """One SQLite transaction; preserve existing roster ownership and Discord links."""
    with sqlite3.connect(ALLIANCE_DB, timeout=30) as adb:
        row = adb.execute(
            "SELECT discord_server_id,kid,game_aid,game_tag FROM alliance_list WHERE alliance_id=?",
            (alliance_id,),
        ).fetchone()
    if row != (guild_id, resolved.state_id, resolved.aid, resolved.tag):
        raise ValueError("Verified alliance or guild changed; import cancelled.")
    added = home = conflicts = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(USERS_DB, timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            for fid, name, furnace, state, power in entries:
                old = db.execute("SELECT alliance FROM users WHERE fid=?", (fid,)).fetchone()
                if old:
                    if old[0] == alliance_id:
                        home += 1
                    else:
                        conflicts += 1
                    continue
                db.execute(
                    "INSERT INTO users (fid,nickname,furnace_lv,kid,stove_lv_content,alliance,"
                    "power,power_updated_at) VALUES (?,?,?,?,NULL,?,?,?)",
                    (fid, name, furnace, state, alliance_id, power, now if power is not None else None),
                )
                added += 1
            db.commit()
        except Exception:
            db.rollback()
            raise
    return RosterResult(len(entries), added, home, conflicts, 0)
