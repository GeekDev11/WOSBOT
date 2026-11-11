"""Player history: persist distinct provider observations, never treat missing as zero."""
import asyncio
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from .wos_intelligence import basic_fields

DB_PATH = Path("db/whiteoutstats.sqlite")


def initialize(path=DB_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS wos_player_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chief_id INTEGER NOT NULL,
                state_id INTEGER,
                nickname TEXT,
                power INTEGER,
                kills INTEGER,
                furnace_level INTEGER,
                vip_level INTEGER,
                source_cached_at REAL NOT NULL,
                observed_at TEXT NOT NULL,
                UNIQUE(chief_id, source_cached_at)
            );
            CREATE INDEX IF NOT EXISTS idx_wos_player_history
            ON wos_player_history(chief_id, source_cached_at DESC);
        """)
        db.commit()


def store_player(chief_id, payload, path=DB_PATH):
    """Only store valid, timestamped source snapshots; return True if newly saved."""
    if not isinstance(payload, dict):
        return False
    raw_time = payload.get("cached_at")
    if isinstance(raw_time, bool) or not isinstance(raw_time, (float, int)) or raw_time <= 0:
        return False
    data = basic_fields(payload)
    if not data or not any(isinstance(data.get(k), int) and not isinstance(data.get(k), bool)
                           for k in ("power", "kills", "furnace")):
        return False
    initialize(path)
    with closing(sqlite3.connect(path, timeout=30)) as db:
        cursor = db.execute(
            "INSERT OR IGNORE INTO wos_player_history "
            "(chief_id, state_id, nickname, power, kills, furnace_level, vip_level, source_cached_at, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(chief_id), data["state"], data["name"], data["power"],
             data["kills"], data["furnace"], data["vip"], float(raw_time),
             datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
        return cursor.rowcount > 0


def load_history(chief_id, limit=30, path=DB_PATH):
    initialize(path)
    with closing(sqlite3.connect(path, timeout=30)) as db:
        return db.execute(
            "SELECT source_cached_at, power, kills, furnace_level, state_id "
            "FROM wos_player_history WHERE chief_id=? "
            "ORDER BY source_cached_at DESC LIMIT ?",
            (int(chief_id), max(1, min(90, int(limit)))),
        ).fetchall()


def delta_text(rows):
    """Calculate only comparable numbers between distinct source snapshots."""
    if len(rows) < 2:
        return "Need at least two distinct API snapshots before growth can be calculated."
    recent, old = rows[0], rows[-1]
    lines = []
    for label, col in (("Power", 1), ("Kills", 2), ("Furnace", 3)):
        a, b = recent[col], old[col]
        if isinstance(a, int) and isinstance(b, int):
            diff = a - b
            sign = "+" if diff >= 0 else ""
            lines.append(f"**{label}:** {sign}{diff:,} ({b:,} → {a:,})")
    return "\n".join(lines) or "No comparable statistics in these snapshots."


class WosPlayerHistory(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="woshistory", description="Compare previously saved Whiteout Stats player snapshots")
    @app_commands.describe(chief_id="In-game Chief ID", observations="Most recent observations to compare")
    async def woshistory(self, interaction: discord.Interaction, chief_id: str, observations: int = 30):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not chief_id.isascii() or not chief_id.isdecimal() or not 1 <= len(chief_id) <= 16:
            await interaction.followup.send("Enter a valid numeric Chief ID.", ephemeral=True)
            return
        if not 2 <= observations <= 90:
            await interaction.followup.send("Observations must be between 2 and 90.", ephemeral=True)
            return
        rows = await asyncio.to_thread(load_history, int(chief_id), observations)
        embed = discord.Embed(title=f"Player history · {chief_id}", description=delta_text(rows))
        embed.add_field(name="Saved observations", value=str(len(rows)))
        embed.set_footer(text="Historical changes reflect source snapshots, not actual login times")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(WosPlayerHistory(bot))
