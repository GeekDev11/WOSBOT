"""Whiteout Stats alliance intelligence: read-only rosters and observed changes.

Never alters WOSLand's existing alliance or member records.
"""
import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from . import alliance_api_sync as sync


DB_PATH = Path("db/whiteoutstats.sqlite")
API_BASE = "https://api.whiteoutstats.com"


def parse_alliance(payload):
    """Validate a complete roster before comparison or persistence.

    A missing members array is NOT an empty roster. The API explicitly provides
    members when include=roster is requested.
    """
    if not isinstance(payload, dict) or payload.get("ok") is False:
        raise ValueError("Unusable API response")
    alliance = payload.get("alliance")
    members = payload.get("members")
    if not isinstance(alliance, dict) or not isinstance(members, list):
        raise ValueError("Alliance information or complete roster not available")
    if alliance.get("aid") is None or alliance.get("kid") is None:
        raise ValueError("Alliance identifier or state missing")
    roster = {}
    for m in members:
        if not isinstance(m, dict):
            raise ValueError("Roster contains an invalid member")
        cid = m.get("chief_id", m.get("fid"))
        if isinstance(cid, bool) or cid is None:
            raise ValueError("Roster contains a member without a chief ID")
        try:
            cid = int(cid)
        except (ValueError, TypeError) as exc:
            raise ValueError("Roster contains an invalid chief ID") from exc
        if cid <= 0 or cid in roster:
            raise ValueError("Roster contains a duplicate or invalid chief ID")
        roster[cid] = {
            "name": str(m.get("nick_name") or cid)[:150],
            "power": m.get("power") if isinstance(m.get("power"), int) else None,
            "state": m.get("kid"),
            "rank": m.get("alliance_rank_label") or m.get("alliance_rank"),
        }
    return alliance, roster


def diff_rosters(previous, current):
    """Report observed membership differences; no inference of a state transfer."""
    joined = sorted(set(current) - set(previous))
    left = sorted(set(previous) - set(current))
    changed_rank = sorted(
        fid for fid in set(previous) & set(current)
        if previous[fid].get("rank") != current[fid].get("rank")
        and previous[fid].get("rank") is not None and current[fid].get("rank") is not None
    )
    return {"joined": joined, "left": left, "rank_changed": changed_rank}


def init_db(path=DB_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS wos_alliance_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state_id INTEGER NOT NULL,
                alliance_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                source_cached_at TEXT,
                observed_at TEXT NOT NULL,
                roster_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wos_alliance_snapshots
            ON wos_alliance_snapshots (state_id, alliance_id, id DESC);
            CREATE TABLE IF NOT EXISTS wos_alliance_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state_id INTEGER NOT NULL,
                alliance_id INTEGER NOT NULL,
                chief_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                observed_at TEXT NOT NULL,
                snapshot_id INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wos_alliance_events
            ON wos_alliance_events (state_id, alliance_id, id DESC);
        """)
        db.commit()


def store_roster(state_id, alliance, roster, cached_at, path=DB_PATH):
    """Atomic snapshot and event write. Identical API cache data is not recorded twice."""
    init_db(path)
    aid = int(alliance["aid"])
    tag = str(alliance.get("abbr") or "")
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.execute("BEGIN IMMEDIATE")
        latest = db.execute(
            "SELECT id, source_cached_at, roster_json FROM wos_alliance_snapshots "
            "WHERE state_id=? AND alliance_id=? ORDER BY id DESC LIMIT 1",
            (state_id, aid),
        ).fetchone()
        if latest and cached_at is not None and str(cached_at) == latest[1]:
            db.commit()
            return {"stored": False, "reason": "same source snapshot", "events": None}
        prev = {int(k): v for k, v in json.loads(latest[2]).items()} if latest else None
        delta = diff_rosters(prev, roster) if prev is not None else None
        cursor = db.execute(
            "INSERT INTO wos_alliance_snapshots "
            "(state_id, alliance_id, tag, source_cached_at, observed_at, roster_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (state_id, aid, tag, str(cached_at) if cached_at is not None else None,
             now, json.dumps(roster, ensure_ascii=False)),
        )
        sid = cursor.lastrowid
        if delta is not None:
            for event_type, ids in delta.items():
                for fid in ids:
                    old = (prev.get(fid) or {}).get("rank") if event_type == "rank_changed" else None
                    new = (roster.get(fid) or {}).get("rank") if event_type == "rank_changed" else None
                    db.execute(
                        "INSERT INTO wos_alliance_events "
                        "(state_id, alliance_id, chief_id, event_type, old_value, new_value, observed_at, snapshot_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (state_id, aid, fid, event_type,
                         str(old) if old is not None else None, str(new) if new is not None else None,
                         now, sid),
                    )
        db.commit()
    return {"stored": True, "reason": "initial baseline" if delta is None else "compared",
            "events": delta}


def recent_events(state_id, alliance_id, limit=25, path=DB_PATH):
    init_db(path)
    with closing(sqlite3.connect(path, timeout=30)) as db:
        return db.execute(
            "SELECT chief_id, event_type, old_value, new_value, observed_at "
            "FROM wos_alliance_events WHERE state_id=? AND alliance_id=? "
            "ORDER BY id DESC LIMIT ?",
            (state_id, alliance_id, min(50, max(1, int(limit)))),
        ).fetchall()


class WosAllianceIntel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def fetch_roster(self, state_id, tag):
        profile, key = await asyncio.to_thread(sync.load_connection)
        if not key or not sync.api_available(profile):
            return None, "Configure Whiteout Stats under Player Sync first."
        if not await sync._take_slot(profile):
            return None, "Configured API request budget is exhausted."
        cog = self.bot.get_cog("AllianceApiSync")
        if cog is None:
            return None, "Player Sync is not loaded."
        url = f"{API_BASE}/v1/alliances/{state_id}/{quote(tag, safe='')}?include=info,roster"
        try:
            async with cog.session().get(
                url, headers={"Accept": "application/json", profile["auth_header"]: key},
                timeout=sync.REQUEST_TIMEOUT,
            ) as response:
                if response.status != 200:
                    return None, f"Whiteout Stats returned HTTP {response.status}."
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None, "The API did not return usable JSON."
        try:
            alliance, roster = parse_alliance(payload)
        except ValueError as exc:
            return None, str(exc)
        if int(alliance["kid"]) != state_id or alliance.get("abbr") != tag:
            return None, "API alliance identity does not match the requested state and tag."
        return (payload, alliance, roster), None

    @app_commands.command(name="wosalliance", description="Inspect an alliance roster using Whiteout Stats")
    @app_commands.describe(state_id="State number", tag="Case-sensitive alliance tag")
    async def wosalliance(self, interaction: discord.Interaction, state_id: int, tag: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if state_id <= 0 or not tag or len(tag) > 32:
            await interaction.followup.send("Enter a positive state number and valid alliance tag.", ephemeral=True)
            return
        found, error = await self.fetch_roster(state_id, tag)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        payload, alliance, roster = found
        result = await asyncio.to_thread(
            store_roster, state_id, alliance, roster, payload.get("cached_at"))
        embed = discord.Embed(title=f"[{tag}] {alliance.get('name') or 'Alliance'}")
        embed.add_field(name="State", value=str(state_id))
        embed.add_field(name="Members in API roster", value=str(len(roster)))
        embed.add_field(name="Power", value=f"{alliance['power']:,}" if isinstance(alliance.get("power"), int) else "Not reported")
        embed.add_field(name="Leader", value=str(alliance.get("leader_name") or "Not reported")[:1024])
        top = sorted(roster.items(), key=lambda row: row[1]["power"] or 0, reverse=True)[:10]
        embed.add_field(name="Top members by power", value="\n".join(
            f"`{fid}` · {data['name']} · {data['power']:,}" if isinstance(data["power"], int)
            else f"`{fid}` · {data['name']}" for fid, data in top
        )[:1024] or "No members reported", inline=False)
        delta = result["events"]
        if delta is not None:
            embed.add_field(name="Changes since last observation",
                            value=f"Joined: {len(delta['joined'])} · Left: {len(delta['left'])} · Rank changes: {len(delta['rank_changed'])}",
                            inline=False)
        embed.set_footer(text="API observation only · Missing members are not confirmed state transfers")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="wosalliancechanges", description="See observed roster changes from saved API snapshots")
    @app_commands.describe(state_id="State number", alliance_id="Internal alliance ID from Whiteout Stats")
    async def wosalliancechanges(self, interaction: discord.Interaction, state_id: int, alliance_id: int):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if state_id <= 0 or alliance_id <= 0:
            await interaction.followup.send("State and alliance IDs must be positive.", ephemeral=True)
            return
        rows = await asyncio.to_thread(recent_events, state_id, alliance_id)
        if not rows:
            await interaction.followup.send("No observed changes yet. Two distinct roster snapshots are needed.", ephemeral=True)
            return
        titles = {"joined": "Joined", "left": "Left roster", "rank_changed": "Rank changed"}
        embed = discord.Embed(title=f"Alliance changes · State {state_id} · Alliance {alliance_id}")
        embed.description = "\n".join(
            f"**{titles.get(kind, kind)}** · `{fid}`"
            + (f" ({old} → {new})" if kind == "rank_changed" else "")
            for fid, kind, old, new, observed in rows
        )[:4000]
        embed.set_footer(text="Observed roster differences, not proof of state transfers")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(WosAllianceIntel(bot))
