"""Read-only Whiteout Stats profile and shield commands.

Reuses the configured Player Sync connection, quota and HTTP client.
No Discord bot token or API key belongs in this module.
"""
import asyncio
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


def with_include(profile, sections):
    """Use include sections without mutating the saved Player Sync profile."""
    updated = dict(profile)
    parsed = urlsplit(profile["player_path"])
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "include"]
    query.append(("include", ",".join(sections)))
    updated["player_path"] = urlunsplit(("", "", parsed.path, urlencode(query), ""))
    return updated


def freshness(payload):
    age = payload.get("age_seconds") if isinstance(payload, dict) else None
    if isinstance(age, (int, float)) and not isinstance(age, bool) and age >= 0:
        return f"API snapshot age: {round(age / 60)} min"
    return "API snapshot age unknown"


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
        "kills": player.get("kills"),
        "vip": player.get("vip"),
        "alliance": player.get("alliance"),
    }


def arena_heroes(payload):
    """Arena defence only; absent data is not a complete inventory."""
    heroes = payload.get("heroes") if isinstance(payload, dict) else None
    if isinstance(heroes, dict):
        heroes = heroes.get("items") or heroes.get("heroes")
    return [hero for hero in heroes if isinstance(hero, dict)] if isinstance(heroes, list) else []


def chief_equipment(payload):
    """Return equipment or a clear hidden/unavailable state."""
    gear = payload.get("chief_gear") if isinstance(payload, dict) else None
    if not isinstance(gear, dict):
        return None, "Chief gear not reported"
    if gear.get("hidden") is True:
        return [], "Chief equipment hidden"
    items = gear.get("items")
    if not isinstance(items, list):
        return [], "Chief gear items not reported"
    return [item for item in items if isinstance(item, dict)], None


def short(value, fallback="—", limit=100):
    return str(value)[:limit] if value is not None else fallback


def describe_error(status):
    return {
        0: "The API connection timed out or failed.",
        401: "Whiteout Stats rejected the configured API key.",
        403: "This API key cannot access the requested data.",
        404: "Player not found.",
        429: "The API rate limit was reached.",
    }.get(status, f"Whiteout Stats returned HTTP {status}.")



def activity_timestamp(value):
    """Accept plausible Unix seconds, but do not invent a login time."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        timestamp = int(value)
        if timestamp <= 0:
            return None
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return timestamp
    except (ValueError, OverflowError, OSError):
        return None


def check_embed(payload, chief_id):
    """Show the API's reported activity, never claim verified game presence."""
    data = basic_fields(payload)
    if not data:
        return None
    player = payload["player"]
    online = player.get("online")
    status = ("🟢 Reported online" if online is True else
              "⚪ Reported offline" if online is False else
              "Not reported")
    last_login = player.get("last_login")
    timestamp = activity_timestamp(player.get("last_active_at"))
    embed = discord.Embed(
        title=short(data["name"], f"Chief {chief_id}", 256),
        description=f"👀 **Chief Stalker** · Chief ID: `{chief_id}`",
        color=discord.Color.green() if online is True else discord.Color.blurple(),
    )
    embed.add_field(name="Activity status", value=status, inline=True)
    embed.add_field(
        name="Reported last active",
        value=(f"<t:{timestamp}:R>" if timestamp is not None
               else short(last_login, "Not reported", 200)),
        inline=True,
    )
    if timestamp is not None:
        embed.add_field(name="Recorded activity time", value=f"<t:{timestamp}:F>", inline=False)
    for label, key in (("State", "state"), ("Power", "power"), ("Kills", "kills"), ("VIP", "vip")):
        value = data[key]
        formatted = f"{value:,}" if type(value) is int else short(value, "Not reported", 80)
        embed.add_field(name=label, value=formatted, inline=True)
    furnace = data["furnace"]
    embed.add_field(
        name="Furnace",
        value=format_furnace_level(furnace) if type(furnace) is int else "Not reported",
        inline=True,
    )
    alliance = data["alliance"]
    if isinstance(alliance, dict):
        abbr, name = alliance.get("abbr"), alliance.get("name")
    else:
        abbr, name = player.get("alliance_abbr"), player.get("alliance_name")
    if abbr or name:
        embed.add_field(
            name="Alliance",
            value=f"[{short(abbr, '?', 30)}] {short(name, '', 150)}"[:1024],
            inline=False,
        )
    coords = (f"X: {data['x']} · Y: {data['y']}"
              if data["x"] is not None and data["y"] is not None else "Not reported")
    embed.add_field(name="Last reported coordinates", value=coords, inline=False)
    avatar = player.get("avatar_url")
    if isinstance(avatar, str) and avatar.startswith(("https://", "http://")):
        embed.set_thumbnail(url=avatar)
    embed.set_footer(
        text=f"Whiteout Stats · {freshness(payload)} · Activity may be stale or inaccurate"
    )
    return embed


class CheckRefreshView(discord.ui.View):
    """User-scoped refresh; reuses the existing API client and quota limits."""

    def __init__(self, cog, chief_id, requester_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.chief_id = chief_id
        self.requester_id = requester_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the person who ran /check can refresh this result.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        payload, error = await self.cog.get_player(self.chief_id)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        embed = check_embed(payload, self.chief_id)
        if embed is None:
            await interaction.followup.send("No player object was found in the API response.", ephemeral=True)
            return
        await interaction.edit_original_response(embed=embed, view=self)


class ChiefStalker(commands.Cog):
    """Additional read-only API commands; does not alter Player Sync records."""

    def __init__(self, bot):
        self.bot = bot

    async def get_player(self, chief_id, include=("base",)):
        profile, key = await asyncio.to_thread(sync.load_connection)
        if not key or not sync.api_available(profile):
            return None, "Configure Player Sync and its API Key first."
        if not await sync._take_slot(profile):
            return None, "The configured daily API request limit has been reached."
        existing = self.bot.get_cog("AllianceApiSync")
        if existing is None:
            return None, "Player Sync is not loaded."
        # Keep the same configured endpoint and credentials, including query options.
        answer = await sync.fetch_player(existing.session(), with_include(profile, include), key, chief_id)
        if answer.status != 200:
            return None, describe_error(answer.status)
        if not isinstance(answer.payload, dict):
            return None, "The API returned no usable JSON object."
        return answer.payload, None

    @app_commands.command(name="check", description="Check a chief's reported activity and profile")
    @app_commands.describe(chief_id="In-game Chief ID")
    async def check(self, interaction: discord.Interaction, chief_id: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not chief_id.isascii() or not chief_id.isdecimal() or not (1 <= len(chief_id) <= 16):
            await interaction.followup.send("Enter a valid numeric Chief ID.", ephemeral=True)
            return
        numeric_id = int(chief_id)
        payload, error = await self.get_player(numeric_id)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        embed = check_embed(payload, numeric_id)
        if embed is None:
            await interaction.followup.send("No player object was found in the API response.", ephemeral=True)
            return
        await interaction.followup.send(
            embed=embed,
            view=CheckRefreshView(self, numeric_id, interaction.user.id),
            ephemeral=True,
        )

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
        from .wos_player_history import store_player
        await asyncio.to_thread(store_player, int(chief_id), payload)
        embed = discord.Embed(title=str(data["name"] or f"Chief {chief_id}")[:256], description=f"Chief ID: `{chief_id}`")
        for title, key in (("State", "state"), ("Power", "power")):
            value = data[key]
            formatted = f"{value:,}" if key == "power" and isinstance(value, int) else str(value)
            embed.add_field(name=title, value=formatted if value is not None else "Not reported", inline=True)
        for label, key in (("Kills", "kills"), ("VIP", "vip")):
            value = data[key]
            displayed = f"{value:,}" if key == "kills" and isinstance(value, int) else str(value)
            embed.add_field(name=label, value=displayed if value is not None else "Not reported", inline=True)
        alliance = data["alliance"]
        if isinstance(alliance, dict):
            tag = alliance.get("abbr")
            alliance_name = alliance.get("name")
            if tag or alliance_name:
                embed.add_field(name="Alliance", value=f"[{tag or '?'}] {alliance_name or ''}"[:1024], inline=False)
        level = data["furnace"]
        embed.add_field(name="Furnace", value=format_furnace_level(level) if isinstance(level, int) else "Not reported", inline=True)
        if data["x"] is not None and data["y"] is not None:
            embed.add_field(name="Last reported map coordinates", value=f"X: {data['x']} · Y: {data['y']}", inline=False)
        else:
            embed.add_field(name="Map coordinates", value="Not reported", inline=False)
        embed.add_field(name="Shield", value=format_shield(data["shield"]), inline=False)
        embed.set_footer(text=f"Whiteout Stats · {freshness(payload)} · Not live data")
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
        embed.set_footer(text=f"{freshness(payload)} · The player may have refreshed their shield")
        await interaction.followup.send(embed=embed, ephemeral=True)


    @app_commands.command(name="wosheroes", description="Show a player's reported arena defence heroes")
    @app_commands.describe(chief_id="In-game Chief ID")
    async def wosheroes(self, interaction: discord.Interaction, chief_id: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not chief_id.isascii() or not chief_id.isdecimal() or not (1 <= len(chief_id) <= 16):
            await interaction.followup.send("Enter a valid numeric Chief ID.", ephemeral=True)
            return
        payload, error = await self.get_player(int(chief_id), include=("base", "heroes"))
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        heroes = arena_heroes(payload)
        if not heroes:
            await interaction.followup.send("No arena defence heroes were reported by the API.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Arena Defence · Chief {chief_id}")
        for number, hero in enumerate(heroes[:5], 1):
            name = short(hero.get("name"), "Unnamed hero", 120)
            level = short(hero.get("level"))
            stars = short(hero.get("star_label") or hero.get("stars") or hero.get("star"))
            power = hero.get("power")
            weapon = hero.get("exclusive_gear_level")
            skills = hero.get("skill_levels")
            gear = hero.get("gear")
            skill_text = ", ".join(short(skill.get("level")) for skill in skills if isinstance(skill, dict)) if isinstance(skills, list) else "—"
            equipped = len([piece for piece in gear if isinstance(piece, dict)]) if isinstance(gear, list) else 0
            value = (
                f"Level: {level} · Stars: {stars}\n"
                f"Power: {power:,}" if isinstance(power, int) else f"Level: {level} · Stars: {stars}\nPower: —"
            )
            value += f"\nSkills: {skill_text[:180]} · Exclusive weapon: {short(weapon)}\nGear pieces: {equipped}"
            embed.add_field(name=f"{number}. {name}", value=value[:1024], inline=False)
        embed.set_footer(text=f"{freshness(payload)} · Arena defence lineup only")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="wosgear", description="Show a player's reported chief equipment")
    @app_commands.describe(chief_id="In-game Chief ID")
    async def wosgear(self, interaction: discord.Interaction, chief_id: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not chief_id.isascii() or not chief_id.isdecimal() or not (1 <= len(chief_id) <= 16):
            await interaction.followup.send("Enter a valid numeric Chief ID.", ephemeral=True)
            return
        payload, error = await self.get_player(int(chief_id), include=("base", "chief_gear"))
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        items, reason = chief_equipment(payload)
        if reason:
            await interaction.followup.send(f"{reason}. {freshness(payload)}.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Chief Equipment · {chief_id}")
        for item in items[:20]:
            label = short(item.get("name") or item.get("slot"), "Equipment", 120)
            parts = [f"Tier: {short(item.get('tier'))}", f"Stars: {short(item.get('star'))}",
                     f"Strength: {short(item.get('strength_level'))}", f"Score: {short(item.get('score'))}"]
            gems = item.get("gems")
            if isinstance(gems, list):
                parts.append(f"Gems: {len(gems)}")
            embed.add_field(name=label, value=" · ".join(parts)[:1024], inline=False)
        if not items:
            embed.description = "No chief equipment items reported."
        embed.set_footer(text=f"{freshness(payload)} · Last reported equipment")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(ChiefStalker(bot))
