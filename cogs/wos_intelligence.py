"""Read-only Whiteout Stats profile and shield commands.

Reuses the configured Player Sync connection, quota and HTTP client.
No Discord bot token or API key belongs in this module.
"""
import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from . import alliance_api_sync as sync
from .bot_level_mapping import format_furnace_level


def format_shield(endtime, now=None):
    """Return a safe, human-readable description of the recorded shield expiry."""
    if isinstance(endtime, bool) or endtime is None:
        return "Not reported"
    try:
        timestamp = int(endtime)
        if timestamp <= 0:
            return "Not reported"
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return "Invalid API value"
    if now is None:
        now = datetime.now(timezone.utc).timestamp()
    label = "Last reported expiry" if timestamp <= now else "Reported expiry"
    return f"{label}: <t:{timestamp}:F> (<t:{timestamp}:R>)"


def basic_fields(payload):
    """Extract known data without interpreting absent fields as zero."""
    player = payload.get("player") if isinstance(payload, dict) else None
    if not isinstance(player, dict):
        return {}
    return {
        "name": player.get("nick_name"),
        "state": player.get("kid"),
        "furnace": player.get("furnace_level"),
        "power": player.get("power"),
        "x": player.get("x"),
        "y": player.get("y"),
        "shield": player.get("shield_endtime"),
    }


def describe_error(status):
    return {
        0: "The API connection timed out or failed.",
        401: "Whiteout Stats rejected the configured API key.",
        403: "This API key cannot access the requested data.",
        404: "Player not found.",
        429: "The API rate limit was reached.",
    }.get(status, f"Whiteout Stats returned HTTP {status}.")


class WosIntelligence(commands.Cog):
    """Additional read-only API commands; does not alter Player Sync records."""

    def __init__(self, bot):
        self.bot = bot

    async def get_player(self, chief_id):
        profile, key = await asyncio.to_thread(sync.load_connection)
        if not key or not sync.api_available(profile):
            return None, "Configure Player Sync and its API Key first."
        if not await sync._take_slot(profile):
            return None, "The configured daily API request limit has been reached."
        existing = self.bot.get_cog("AllianceApiSync")
        if existing is None:
            return None, "Player Sync is not loaded."
        # Keep the same configured endpoint and credentials, including query options.
        answer = await sync.fetch_player(existing.session(), profile, key, chief_id)
        if answer.status != 200:
            return None, describe_error(answer.status)
        if not isinstance(answer.payload, dict):
            return None, "The API returned no usable JSON object."
        return answer.payload, None

    @app_commands.command(name="wosplayer", description="Inspect a Whiteout Survival player using Whiteout Stats")
    @app_commands.describe(chief_id="In-game Chief ID")
    async def wosplayer(self, interaction: discord.Interaction, chief_id: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not chief_id.isascii() or not chief_id.isdecimal() or not (1 <= len(chief_id) <= 16):
            await interaction.followup.send("Enter a valid numeric Chief ID.", ephemeral=True)
            return
        payload, error = await self.get_player(int(chief_id))
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        data = basic_fields(payload)
        if not data:
            await interaction.followup.send("No player object was found in the API response.", ephemeral=True)
            return
        embed = discord.Embed(title=str(data["name"] or f"Chief {chief_id}")[:256], description=f"Chief ID: `{chief_id}`")
        for title, key in (("State", "state"), ("Power", "power")):
            value = data[key]
            formatted = f"{value:,}" if key == "power" and isinstance(value, int) else str(value)
            embed.add_field(name=title, value=formatted if value is not None else "Not reported", inline=True)
        level = data["furnace"]
        embed.add_field(name="Furnace", value=format_furnace_level(level) if isinstance(level, int) else "Not reported", inline=True)
        if data["x"] is not None and data["y"] is not None:
            embed.add_field(name="Last reported map coordinates", value=f"X: {data['x']} · Y: {data['y']}", inline=False)
        else:
            embed.add_field(name="Map coordinates", value="Not reported", inline=False)
        embed.add_field(name="Shield", value=format_shield(data["shield"]), inline=False)
        embed.set_footer(text="Whiteout Stats API · Cached data; not live shield/location verification")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="wosshield", description="Display the last reported shield expiration")
    @app_commands.describe(chief_id="In-game Chief ID")
    async def wosshield(self, interaction: discord.Interaction, chief_id: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not chief_id.isascii() or not chief_id.isdecimal() or not (1 <= len(chief_id) <= 16):
            await interaction.followup.send("Enter a valid numeric Chief ID.", ephemeral=True)
            return
        payload, error = await self.get_player(int(chief_id))
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        data = basic_fields(payload)
        if not data:
            await interaction.followup.send("No player object was found in the API response.", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"Shield · {str(data['name'] or chief_id)[:220]}",
            description=format_shield(data["shield"]),
        )
        embed.set_footer(text="Last reported API data; the player may have refreshed their shield")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(WosIntelligence(bot))
