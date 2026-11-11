"""Whiteout Stats state leaderboards, using the configured shared API budget."""
import asyncio
from urllib.parse import urlencode

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from . import alliance_api_sync as sync

API_BASE = "https://api.whiteoutstats.com"
BOARDS = (
    "alliance_power", "alliance_kills", "personal_power", "kills", "furnace",
    "rebel_conquest", "single_hero", "hero_total", "troop_power",
    "building_power", "research_power", "hero_no_equip", "hero_equip",
    "chief_gear", "chief_charm", "pet_power", "island_prosperity",
    "state_transfer", "labyrinth", "land_of_heroes", "cave_of_monsters",
    "charm_mine", "research_center", "gear_forge", "gaia_heart", "master_power",
)


def parse_board(payload):
    """Parse rankings without manufacturing scores or identities.

    Accept both a direct 'rankings' list and the documented 'entries' variant
    when present. An unexpected wrapper must be surfaced as unavailable.
    """
    if not isinstance(payload, dict) or payload.get("ok") is False:
        raise ValueError("No usable leaderboard returned")
    entries = payload.get("rankings")
    if entries is None:
        entries = payload.get("entries")
    if entries is None:
        entries = payload.get("players")
    if entries is None:
        entries = payload.get("alliances")
    if not isinstance(entries, list):
        raise ValueError("Leaderboard entries were not present in API response")
    normalized = []
    for i, item in enumerate(entries, 1):
        if not isinstance(item, dict):
            continue
        chief_id = item.get("chief_id")
        aid = item.get("aid")
        rank = item.get("rank", i)
        name = item.get("nick_name") or item.get("name") or item.get("abbr")
        score = item.get("score")
        if name is None:
            name = str(chief_id if chief_id is not None else aid) if chief_id is not None or aid is not None else "Unknown"
        normalized.append({
            "rank": rank, "name": str(name)[:150], "score": score,
            "chief_id": chief_id, "alliance_id": aid,
        })
    return normalized


def summary_text(entries, max_rows=15):
    out = []
    for item in entries[:max_rows]:
        score = item["score"]
        shown = f"{score:,}" if isinstance(score, int) else str(score) if score is not None else "—"
        name = item["name"].replace("  ", " ").replace("`", "'")
        out.append(f"**#{item['rank']}** {name[:65]} — {shown}")
    return "\n".join(out) or "No results reported."


class WosLeaderboards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def fetch_board(self, state_id, board, limit):
        profile, key = await asyncio.to_thread(sync.load_connection)
        if not key or not sync.api_available(profile):
            return None, "Configure Whiteout Stats under Player Sync first."
        if not await sync._take_slot(profile):
            return None, "Configured daily API budget exhausted."
        cog = self.bot.get_cog("AllianceApiSync")
        if cog is None:
            return None, "Player Sync is not loaded."
        query = urlencode({"board": board, "limit": limit})
        url = f"{API_BASE}/v1/states/{state_id}/ranks?{query}"
        try:
            async with cog.session().get(
                url, headers={"Accept": "application/json", profile["auth_header"]: key},
                timeout=sync.REQUEST_TIMEOUT,
            ) as response:
                if response.status != 200:
                    return None, f"Whiteout Stats returned HTTP {response.status}."
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None, "The API did not return usable JSON."
        try:
            return parse_board(data), None
        except ValueError as exc:
            return None, str(exc)

    @app_commands.command(name="wosrankings", description="View Whiteout Stats state leaderboards")
    @app_commands.describe(state_id="State number", board="Leaderboard category", limit="Maximum results, 1 to 100")
    @app_commands.choices(board=[app_commands.Choice(name=b.replace("_", " ").title(), value=b) for b in BOARDS[:25]])
    async def wosrankings(
        self, interaction: discord.Interaction, state_id: int, board: str = "personal_power", limit: int = 20
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if state_id <= 0 or board not in BOARDS or not 1 <= limit <= 100:
            await interaction.followup.send("Enter a positive state, a valid board and a limit between 1 and 100.", ephemeral=True)
            return
        entries, error = await self.fetch_board(state_id, board, limit)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        embed = discord.Embed(
            title=f"State {state_id} · {board.replace('_', ' ').title()}",
            description=summary_text(entries)
        )
        embed.set_footer(text=f"Showing first {min(15, len(entries))} of {len(entries)} returned · API snapshot, not live")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(WosLeaderboards(bot))
