"""Whiteout Stats state leaderboards, using the configured shared API budget."""
import asyncio
import logging
from urllib.parse import urlencode

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from . import alliance_api_sync as sync

API_BASE = "https://api.whiteoutstats.com"
logger = logging.getLogger("alliance")
BOARDS = (
    "alliance_power", "alliance_kills", "personal_power", "kills", "furnace",
    "rebel_conquest", "single_hero", "hero_total", "troop_power",
    "building_power", "research_power", "hero_no_equip", "hero_equip",
    "chief_gear", "chief_charm", "pet_power", "island_prosperity",
    "migrant_score", "labyrinth", "land_of_heroes", "cave_of_monsters",
    "charm_mine", "research_center", "gear_forge", "gaia_heart", "master_power",
)


def leaderboard_rows(data, depth=0):
    """Locate ranked records through documented-style API wrappers."""
    if depth > 6:
        return None
    if isinstance(data, list):
        if not data:
            return []
        if all(isinstance(x, dict) for x in data) and any(
            ("rank" in x or "chief_id" in x or "uid" in x or "aid" in x)
            and ("score" in x or "rank" in x) for x in data
        ):
            return data
        return None
    if not isinstance(data, dict) or data.get("ok") is False:
        return None
    for field in ("rankings", "entries", "players", "alliances", "rows",
                  "results", "top", "items", "list", "data", "board",
                  "leaderboard", "result"):
        child = data.get(field)
        if isinstance(child, (dict, list)):
            rows = leaderboard_rows(child, depth + 1)
            if rows is not None:
                return rows
    return None


def parse_board(payload):
    """Parse ranked players and alliances without fabricating data."""
    entries = leaderboard_rows(payload)
    if entries is None:
        raise ValueError("Leaderboard entries were not present in API response")
    normalized = []
    for i, item in enumerate(entries, 1):
        if not isinstance(item, dict):
            continue
        chief_id = item.get("chief_id", item.get("uid"))
        aid = item.get("aid", item.get("alliance_id"))
        rank = item.get("rank", item.get("position", i))
        name = item.get("nick_name") or item.get("name") or item.get("abbr")
        score = item.get("score", item.get("value"))
        if name is None:
            name = str(chief_id if chief_id is not None else aid) if chief_id is not None or aid is not None else "Unknown"
        normalized.append({"rank": rank, "name": str(name)[:150],
                           "score": score, "chief_id": chief_id,
                           "alliance_id": aid})
    return normalized


def leaderboard_shape(data):
    """Only structural metadata; never log identities or API credentials."""
    if not isinstance(data, dict):
        return type(data).__name__
    parts = ["keys=" + ",".join(sorted(str(k) for k in data)[:25])]
    for key in ("data", "state", "board", "boards", "rankings", "results"):
        value = data.get(key)
        if isinstance(value, dict):
            parts.append(key + ":keys=" + ",".join(sorted(str(k) for k in value)[:15]))
        elif isinstance(value, list):
            parts.append(key + ":list=" + str(len(value)))
    return " ".join(parts)

def summary_text(entries, max_rows=15):
    out = []
    for item in entries[:max_rows]:
        score = item["score"]
        shown = f"{score:,}" if isinstance(score, int) else str(score) if score is not None else "—"
        name = item["name"].replace("  ", " ").replace("`", "'")
        out.append(f"**#{item['rank']}** {name[:65]} — {shown}")
    return "\n".join(out) or "No results reported."


async def BOARD_AUTOCOMPLETE(interaction: discord.Interaction, current: str):
    needle = current.lower().replace(" ", "_")
    return [
        app_commands.Choice(name=board.replace("_", " ").title(), value=board)
        for board in BOARDS if needle in board
    ][:25]


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
            logger.info("State Scout: state %s board %s: %s", state_id, board, leaderboard_shape(data))
            return None, str(exc)

    @app_commands.command(name="wosrankings", description="View Whiteout Stats state leaderboards")
    @app_commands.describe(state_id="State number", board="Leaderboard category", limit="Maximum results, 1 to 100")
    @app_commands.autocomplete(board=BOARD_AUTOCOMPLETE)
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



# State Scout: 23 permitted rankings, 20 positions each. Keep legacy
# /wosrankings unchanged for administrators who use the individual boards.
import time

SCOUT_EXCLUDE = frozenset({"kills", "alliance_kills", "furnace"})
SCOUT_BOARDS = tuple(board for board in BOARDS if board not in SCOUT_EXCLUDE)
SCOUT_LIMIT = 20
SCOUT_CACHE_SECONDS = 300

SCOUT_LABELS = {
    "alliance_power": "Alliance Power",
    "personal_power": "Chief Power",
    "rebel_conquest": "Rebel Conquest",
    "single_hero": "Single Hero Power",
    "hero_total": "Hero Total Power",
    "hero_no_equip": "Hero Without Gear",
    "hero_equip": "Hero Equipment",
    "pet_power": "Pet Power",
    "island_prosperity": "Island Prosperity",
    "master_power": "Expert Power",
    "chief_gear": "Chief Gear",
    "chief_charm": "Chief Charm",
    "migrant_score": "State Transfer Score",
    "labyrinth": "The Labyrinth",
    "land_of_heroes": "Land of Heroes",
    "cave_of_monsters": "Cave of Monsters",
    "charm_mine": "Charm Mine",
    "research_center": "Research Center",
    "gear_forge": "Gear Forge",
    "gaia_heart": "Gaia Heart",
}

def scout_label(board):
    return SCOUT_LABELS.get(board, board.replace("_", " ").title())


def parse_state_boards(payload):
    """Extract only recognized boards, leaving unknown formats to fallback."""
    if not isinstance(payload, dict) or payload.get("ok") is False:
        return {}
    containers = [payload]
    for field in ("state", "data", "result"):
        obj = payload.get(field)
        if isinstance(obj, dict):
            containers.append(obj)
    found = {}
    for container in containers:
        for key in ("boards", "leaderboards", "rankings"):
            raw = container.get(key)
            if isinstance(raw, dict):
                for slug, entries in raw.items():
                    if slug not in SCOUT_BOARDS:
                        continue
                    try:
                        found[slug] = parse_board(entries if isinstance(entries, dict) else {"rankings": entries})[:SCOUT_LIMIT]
                    except ValueError:
                        pass
            elif isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    slug = item.get("board", item.get("slug", item.get("key", item.get("type"))))
                    if slug not in SCOUT_BOARDS:
                        continue
                    try:
                        found[slug] = parse_board(item)[:SCOUT_LIMIT]
                    except ValueError:
                        pass
    return found

def scout_description(entries):
    """Render all top-20 entries within Discord's 4096-char embed limit."""
    rows = summary_text(entries[:SCOUT_LIMIT], max_rows=SCOUT_LIMIT)
    return rows[:4000] if rows else "No results reported."


def scout_embed(state_id, board, entries, *, available=None, error=None):
    title = f"State Scout · #{state_id}"
    description = (f"**{scout_label(board)} · Top {SCOUT_LIMIT}**\n\n"
                   + (error if error else scout_description(entries)))
    embed = discord.Embed(title=title, description=description[:4096])
    embed.set_footer(text=(
        f"Category {SCOUT_BOARDS.index(board)+1}/{len(SCOUT_BOARDS)}"
        f" · {min(SCOUT_LIMIT, len(entries))} returned · API snapshot, not live"
    ))
    return embed


class StateScoutView(discord.ui.View):
    def __init__(self, cog, state_id, requester_id, boards=None, start="personal_power"):
        super().__init__(timeout=600)
        self.cog = cog
        self.state_id = state_id
        self.requester_id = requester_id
        self.cache = dict(boards or {})
        self.errors = {}
        self.board = start
        self.lock = asyncio.Lock()

        selector = discord.ui.Select(
            placeholder="Choose leaderboard category",
            min_values=1, max_values=1, row=0,
            options=[
                discord.SelectOption(
                    label=scout_label(slug)[:100], value=slug,
                    default=(slug == start),
                ) for slug in SCOUT_BOARDS
            ],
        )
        selector.callback = self.select_category
        self.category_selector = selector
        self.add_item(selector)

        prev = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary,
                                 emoji="◀️", row=1)
        nxt = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary,
                                emoji="▶️", row=1)
        prev.callback = self.previous_category
        nxt.callback = self.next_category
        self.add_item(prev)
        self.add_item(nxt)
        self.prev_button = prev
        self.next_button = nxt
        self.paint_controls()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Run /scout yourself to open your own State Scout report.",
                ephemeral=True,
            )
            return False
        return True

    def paint_controls(self):
        idx = SCOUT_BOARDS.index(self.board)
        self.prev_button.disabled = idx == 0
        self.next_button.disabled = idx == len(SCOUT_BOARDS)-1
        for option in self.category_selector.options:
            option.default = option.value == self.board

    async def ensure_board(self, board):
        if board in self.cache:
            return self.cache[board], None
        if board in self.errors:
            return [], self.errors[board]
        entries, error = await self.cog.fetch_board(self.state_id, board, SCOUT_LIMIT)
        if error:
            # Don't cache transient API errors; a later button click can retry.
            return [], error
        self.cache[board] = entries[:SCOUT_LIMIT]
        return self.cache[board], None

    async def change(self, interaction, board):
        if self.lock.locked():
            await interaction.response.send_message(
                "A scout request is already running.", ephemeral=True)
            return
        async with self.lock:
            await interaction.response.defer()
            self.board = board
            entries, error = await self.ensure_board(board)
            self.paint_controls()
            await interaction.edit_original_response(
                embed=scout_embed(self.state_id, board, entries, error=error),
                view=self,
            )

    async def select_category(self, interaction):
        await self.change(interaction, self.category_selector.values[0])

    async def previous_category(self, interaction):
        idx = SCOUT_BOARDS.index(self.board)
        await self.change(interaction, SCOUT_BOARDS[max(0, idx-1)])

    async def next_category(self, interaction):
        idx = SCOUT_BOARDS.index(self.board)
        await self.change(interaction, SCOUT_BOARDS[min(len(SCOUT_BOARDS)-1, idx+1)])


class WosStateScout(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._state_cache = {}

    def board_client(self):
        return self.bot.get_cog("WosLeaderboards")

    async def fetch_combined(self, state_id):
        """Prefer one public state request for all boards; use shared API budget."""
        profile, key = await asyncio.to_thread(sync.load_connection)
        if not key or not sync.api_available(profile):
            return {}, "Configure Whiteout Stats under Player Sync first."
        if not await sync._take_slot(profile):
            return {}, "Configured API request budget exhausted."
        main_cog = self.bot.get_cog("AllianceApiSync")
        if main_cog is None:
            return {}, "Player Sync is not loaded."
        url = f"{API_BASE}/v1/states/{state_id}?include=boards&limit={SCOUT_LIMIT}"
        try:
            async with main_cog.session().get(
                url,
                headers={"Accept": "application/json", profile["auth_header"]: key},
                timeout=sync.REQUEST_TIMEOUT,
            ) as response:
                if response.status != 200:
                    return {}, f"Whiteout Stats returned HTTP {response.status}."
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return {}, "Whiteout Stats did not return usable state data."
        boards = parse_state_boards(payload)
        if not boards:
            logger.info("State Scout: combined state %s: %s", state_id, leaderboard_shape(payload))
        return boards, None

    @app_commands.command(name="scout", description="Scout a state: top 20 across 23 leaderboards")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=False)
    @app_commands.describe(state="State number to scout")
    async def scout(self, interaction: discord.Interaction, state: int):
        if state < 1:
            await interaction.response.send_message(
                "Enter a positive state number.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        client = self.board_client()
        if client is None:
            await interaction.followup.send(
                "Whiteout Stats leaderboards are not loaded.", ephemeral=True)
            return
        now = time.monotonic()
        cached = self._state_cache.get(state)
        if cached and now - cached[0] < SCOUT_CACHE_SECONDS:
            boards = dict(cached[1])
        else:
            boards, error = await self.fetch_combined(state)
            if error:
                await interaction.followup.send(error, ephemeral=True)
                return
            self._state_cache[state] = (now, dict(boards))
            # Avoid unbounded long-lived state caches.
            if len(self._state_cache) > 30:
                self._state_cache = {k: v for k, v in self._state_cache.items()
                                     if now - v[0] < SCOUT_CACHE_SECONDS}
        view = StateScoutView(client, state, interaction.user.id, boards=boards)
        entries, error = await view.ensure_board(view.board)
        await interaction.followup.send(
            embed=scout_embed(state, view.board, entries, error=error),
            view=view, ephemeral=True,
        )

async def setup(bot):
    await bot.add_cog(WosLeaderboards(bot))
    await bot.add_cog(WosStateScout(bot))
