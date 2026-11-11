"""Whiteout Stats SvS dashboard (Frosty entitlement required)."""
import asyncio

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from . import alliance_api_sync as sync

API_BASE = "https://api.whiteoutstats.com"


def parse_matchup(payload):
    if not isinstance(payload, dict) or payload.get("ok") is False:
        raise ValueError("SvS matchup not available")
    state = payload.get("state")
    opponent = payload.get("opponent")
    if not isinstance(state, dict):
        raise ValueError("SvS state missing")
    return {
        "season": payload.get("season"),
        "stage": payload.get("stage"),
        "bye": payload.get("bye") is True,
        "state": state.get("kid"),
        "opponent": opponent.get("kid") if isinstance(opponent, dict) else None,
    }


def parse_scores(payload):
    if not isinstance(payload, dict) or payload.get("ok") is False:
        raise ValueError("SvS score data unavailable")
    days = payload.get("days")
    if not isinstance(days, list):
        raise ValueError("SvS day score list missing")
    return {
        "season": payload.get("season"),
        "state": payload.get("state"),
        "opponent": payload.get("opponent"),
        "score": payload.get("score"),
        "opponent_score": payload.get("opponent_score"),
        "days": [day for day in days if isinstance(day, dict)][:5],
    }


def show_score(value):
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else "Not reported"


class WosSvs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def fetch(self, state_id, scores=False):
        profile, key = await asyncio.to_thread(sync.load_connection)
        if not key or not sync.api_available(profile):
            return None, "Configure Whiteout Stats under Player Sync first."
        if not await sync._take_slot(profile):
            return None, "Configured API request budget exhausted."
        cog = self.bot.get_cog("AllianceApiSync")
        if cog is None:
            return None, "Player Sync is not loaded."
        endpoint = "svs/scores" if scores else "svs"
        url = f"{API_BASE}/v1/states/{state_id}/{endpoint}"
        try:
            async with cog.session().get(
                url, headers={"Accept": "application/json", profile["auth_header"]: key},
                timeout=sync.REQUEST_TIMEOUT,
            ) as response:
                if response.status == 403:
                    return None, "SvS API access requires an active Frosty subscription."
                if response.status == 404:
                    return None, "No published SvS matchup or scores are available for this state."
                if response.status != 200:
                    return None, f"Whiteout Stats returned HTTP {response.status}."
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None, "Whiteout Stats did not return usable JSON."
        try:
            return parse_scores(payload) if scores else parse_matchup(payload), None
        except ValueError as exc:
            return None, str(exc)

    @app_commands.command(name="wossvs", description="View an SvS matchup from Whiteout Stats (Frosty)")
    @app_commands.describe(state_id="State number")
    async def wossvs(self, interaction: discord.Interaction, state_id: int):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if state_id <= 0:
            await interaction.followup.send("Enter a positive state number.", ephemeral=True)
            return
        info, error = await self.fetch(state_id)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        embed = discord.Embed(title=f"SvS matchup · State {state_id}")
        embed.add_field(name="Season", value=str(info["season"] or "Not reported"))
        embed.add_field(name="Stage", value=str(info["stage"] or "Not reported"))
        embed.add_field(name="Opponent", value="Bye" if info["bye"] else str(info["opponent"] or "Not announced"))
        embed.set_footer(text="Whiteout Stats · Frosty API")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="wossvsscores", description="View preparation scores from Whiteout Stats (Frosty)")
    @app_commands.describe(state_id="State number")
    async def wossvsscores(self, interaction: discord.Interaction, state_id: int):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if state_id <= 0:
            await interaction.followup.send("Enter a positive state number.", ephemeral=True)
            return
        info, error = await self.fetch(state_id, scores=True)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        embed = discord.Embed(
            title=f"SvS preparation · {state_id} vs {info['opponent']}",
            description=f"Total: {show_score(info['score'])} vs {show_score(info['opponent_score'])}",
        )
        for day in info["days"]:
            embed.add_field(
                name=f"Day {day.get('name') or day.get('day') or '?'} · {day.get('status') or 'Unknown'}",
                value=f"{show_score(day.get('state_score'))} vs {show_score(day.get('opponent_score'))}",
                inline=False,
            )
        embed.set_footer(text="Scores are cached and typically refreshed by the provider every five minutes")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(WosSvs(bot))
