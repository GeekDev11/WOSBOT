"""Unified WOSBOT navigation. Routes into the existing, tested feature handlers.

The command center does not own member mutations, upload processing or API
credentials: those remain in the original cogs and their permission checks.
"""
import asyncio
import logging
import sqlite3

import discord

from .permission_handler import PermissionManager
from .pimp_my_bot import theme, safe_edit_message, menu_timeout

logger = logging.getLogger("bot")

# Ordered, stable menu catalog. Every item has a real handler below.
SECTIONS = {
    "alliance": ("Alliance Command Center", "🛡️", (
        ("overview", "Alliance Overview", "Member totals and alliance summary"),
        ("members", "Members & Imports", "Manage, edit, transfer and import members"),
        ("troops", "Troop Quality", "Recorded tiers and troop submissions"),
        ("imports", "Export Members", "Export alliance roster and selected columns"),
        ("history", "Rankings & History", "Alliance rankings and recorded changes"),
        ("management", "Manage Alliances", "Add alliances, transfers and registration"),
        ("settings", "Alliance Configuration", "Name, state, channels and transfer rules"),
    )),
    "intel": ("Intelligence Center", "🎯", (
        ("scout", "State Scout", "Top 20 rankings across 23 categories"),
        ("player", "Player Inspector", "Chief profile, heroes and equipment"),
        ("allianceintel", "Alliance Intelligence", "External alliance roster"),
        ("playerhistory", "Player History", "Compare recorded player snapshots"),
        ("statecompare", "State Comparison", "Inspect rankings of each state"),
        ("svs", "SvS Intelligence", "Matchup and preparation scores"),
        ("coordinates", "Map & Coordinates", "Available recorded player location"),
    )),
    "events": ("Events & Scheduling", "📅", (
        ("calendar", "Schedule Boards", "Live boards for upcoming events"),
        ("notifications", "Notifications", "Configure notifications and channels"),
        ("bear", "Bear Tracking", "Damage, screenshots and history"),
        ("minister", "Minister Scheduling", "Appointments and archives"),
        ("attendance", "Attendance Management", "Record and correct participation"),
        ("reports", "Event Reports", "Recorded attendance reports and exports"),
    )),
    "gifts": ("Gift Code Center", "🎁", (
        ("dashboard", "Manage Gift Codes", "Add, list or remove gift codes"),
        ("redeem", "Redeem Gift Codes", "Run the existing redemption flow"),
        ("results", "Redemption Results", "View completed and pending redemptions"),
        ("automation", "Gift Code Settings", "Redemption automation, priority and validation"),
        ("channels", "Gift Channels", "Announcement and redemption logs"),
    )),
    "admin": ("Administration & Settings", "⚙️", (
        ("permissions", "Permission Management", "Owners, roles and alliance scope"),
        ("sync", "Player Sync & API", "Connection, limits and scheduled sync"),
        ("appearance", "Appearance & Bot Profile", "Themes and customization"),
        ("health", "Bot Health", "Monitoring and running operations"),
        ("backup", "Backup & Restore", "Backup and restore utilities"),
        ("updates", "Updates & Support", "Updates, bot presence, support and OCR service"),
        ("servers", "Discord Server Settings", "Channel and server configuration"),
    )),
}


async def authorized(interaction, owner_id):
    if interaction.user.id != owner_id:
        await interaction.response.send_message(
            "This menu belongs to another administrator. Open /settings for your own menu.",
            ephemeral=True,
        )
        return False
    permitted, _ = PermissionManager.is_admin(interaction.user.id)
    if not permitted:
        await interaction.response.send_message("Administrator permission required.", ephemeral=True)
        return False
    return True


async def missing(interaction, label):
    await interaction.response.send_message(
        f"{theme.deniedIcon} {label} is not available. Check Bot Health → Modules.",
        ephemeral=True,
    )


async def open_cog(cog, interaction, name, method, *args):
    if name == "AllianceInsights":
        return await show_alliance_insights(cog, interaction, *args)
    other = cog.bot.get_cog(name)
    action = getattr(other, method, None) if other is not None else None
    if not callable(action):
        await missing(interaction, name)
        return
    try:
        await action(interaction, *args)
    except Exception:
        logger.exception("Command Center route failed: %s.%s", name, method)
        if not interaction.response.is_done():
            await interaction.response.send_message("This tool could not open. Check Bot Health logs.", ephemeral=True)
        else:
            await interaction.followup.send("This tool could not open. Check Bot Health logs.", ephemeral=True)


async def invoke_slash(cog, interaction, cog_name, command_name, **values):
    """Dispatch through the original command callback, preserving API budget code."""
    target = cog.bot.get_cog(cog_name)
    command = getattr(type(target), command_name, None) if target else None
    callback = getattr(command, "callback", None)
    if callback is None:
        await missing(interaction, command_name)
        return
    try:
        await callback(target, interaction, **values)
    except Exception:
        logger.exception("Command Center slash route failed: %s.%s", cog_name, command_name)
        if not interaction.response.is_done():
            await interaction.response.send_message("Unable to open this tool. Check Bot Health.", ephemeral=True)
        else:
            await interaction.followup.send("Unable to open this tool. Check Bot Health.", ephemeral=True)



class ResultNavigationView(discord.ui.View):
    """Return from a lookup result to its immediate center or the Command Center."""
    def __init__(self, cog, user_id, section="intel"):
        super().__init__(timeout=menu_timeout())
        self.cog, self.user_id, self.section = cog, user_id, section

    async def interaction_check(self, interaction):
        return await authorized(interaction, self.user_id)

    @discord.ui.button(label="Back to Intelligence", emoji="↩️",
                       style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await open_section(self.cog, interaction, self.section)

    @discord.ui.button(label="Main Menu", emoji="🏠",
                       style=discord.ButtonStyle.secondary)
    async def home(self, interaction, button):
        await self.cog.show_main_menu(interaction)


def intelligence_result_view(interaction):
    """Navigation for standalone and Command Center intelligence responses."""
    center = interaction.client.get_cog("MainMenu")
    return ResultNavigationView(center, interaction.user.id) if center else None


async def send_lookup_result(cog, interaction, *, embed=None, content=None):
    """Always attach working navigation, including empty/API-limit results."""
    await interaction.followup.send(
        content=content, embed=embed, ephemeral=True,
        view=ResultNavigationView(cog, interaction.user.id),
    )


class ChiefLookupModal(discord.ui.Modal):
    chief_id = discord.ui.TextInput(label="Chief ID", placeholder="Enter Chief ID", max_length=24)

    def __init__(self, cog, route):
        super().__init__(title="Find Chief")
        self.cog, self.route = cog, route

    async def on_submit(self, interaction):
        fid = str(self.chief_id.value).strip()
        if not fid.isdigit() or int(fid) < 1:
            await interaction.response.send_message("Enter a positive numeric Chief ID.", ephemeral=True)
            return
        if self.route == "playerhistory":
            await invoke_slash(self.cog, interaction, "WosPlayerHistory", "woshistory", chief_id=fid)
        else:
            await invoke_slash(self.cog, interaction, "ChiefStalker", "wosplayer", chief_id=fid)


class StateLookupModal(discord.ui.Modal):
    state_id = discord.ui.TextInput(label="State number", placeholder="Enter state number", max_length=8)

    def __init__(self, cog, route):
        super().__init__(title="State Intelligence")
        self.cog, self.route = cog, route

    async def on_submit(self, interaction):
        state = str(self.state_id.value).strip()
        if not state.isdigit() or int(state) < 1:
            await interaction.response.send_message("Enter a positive state number.", ephemeral=True)
            return
        key = "scout" if self.route == "scout" else "wossvs"
        target = "WosStateScout" if key == "scout" else "WosSvs"
        await invoke_slash(self.cog, interaction, target, key,
                           **({"state": int(state)} if key == "scout" else {"state_id": int(state)}))


class StateCompareModal(discord.ui.Modal):
    """Collect the state pair once; category changes do not reopen a form."""
    def __init__(self, cog, first=None, second=None):
        super().__init__(title="Compare State Rankings")
        self.cog = cog
        self.first = discord.ui.TextInput(
            label="First state", placeholder="Enter first state number",
            max_length=8, default=str(first) if first is not None else None,
        )
        self.second = discord.ui.TextInput(
            label="Second state", placeholder="Enter second state number",
            max_length=8, default=str(second) if second is not None else None,
        )
        self.add_item(self.first)
        self.add_item(self.second)

    async def on_submit(self, interaction):
        one, two = str(self.first.value).strip(), str(self.second.value).strip()
        if not one.isascii() or not one.isdecimal() or not two.isascii() or not two.isdecimal():
            await interaction.response.send_message("Enter two positive numeric state numbers.", ephemeral=True)
            return
        if min(int(one), int(two)) < 1:
            await interaction.response.send_message("State numbers must be positive.", ephemeral=True)
            return
        view = StateComparisonCategoryView(self.cog, interaction.user.id, int(one), int(two))
        await safe_edit_message(
            interaction, embed=view.build_embed(), view=view, content=None,
        )


class StateComparisonCategoryView(discord.ui.View):
    """Keep the selected states while browsing available leaderboard categories."""
    def __init__(self, cog, user_id, first, second):
        super().__init__(timeout=menu_timeout())
        from .wos_leaderboards import SCOUT_BOARDS, scout_label
        self.cog, self.user_id = cog, user_id
        self.first, self.second = int(first), int(second)
        self.board = None
        self.cache = {}
        self.lock = asyncio.Lock()
        select = discord.ui.Select(
            placeholder="Choose a ranking category",
            row=0,
            options=[discord.SelectOption(label=scout_label(slug)[:100], value=slug)
                     for slug in SCOUT_BOARDS],
        )
        self.category_selector = select
        select.callback = self.select_category
        self.add_item(select)

    def build_embed(self, lines=None):
        from .wos_leaderboards import scout_label
        heading = (f"State {self.first} vs State {self.second}"
                   + (f" · {scout_label(self.board)}" if self.board else ""))
        description = (f"**States:** {self.first} and {self.second}\n"
                       "Choose a ranking category below to compare their top 20 entries. "
                       "Switch categories without entering the states again.")
        if lines:
            description += "\n\n" + "\n".join(lines)
        embed = discord.Embed(title=heading, description=description, color=theme.emColor1)
        embed.set_footer(text="Whiteout Stats snapshots · API usage is limited by your configured budget")
        return embed

    async def interaction_check(self, interaction):
        return await authorized(interaction, self.user_id)

    async def select_category(self, interaction):
        if self.lock.locked():
            await interaction.response.send_message("A comparison is already running.", ephemeral=True)
            return
        from .wos_leaderboards import SCOUT_BOARDS
        board = self.category_selector.values[0]
        if board not in SCOUT_BOARDS:
            await interaction.response.send_message("Unsupported ranking category.", ephemeral=True)
            return
        async with self.lock:
            await interaction.response.defer()
            self.board = board
            if board in self.cache:
                lines = self.cache[board]
            else:
                client = self.cog.bot.get_cog("WosLeaderboards")
                if client is None:
                    lines = ["Whiteout Stats leaderboards are not loaded."]
                else:
                    try:
                        if hasattr(client, "comparison_board"):
                            results = await asyncio.gather(
                                client.comparison_board(self.first, board),
                                client.comparison_board(self.second, board),
                            )
                        else:
                            # Compatibility for older embedded integrations.
                            original = await asyncio.gather(
                                client.fetch_board(self.first, board, 20),
                                client.fetch_board(self.second, board, 20),
                            )
                            results = [(entries, error, None) for entries, error in original]
                        lines = []
                        errors = []
                        for state, (entries, error, source) in zip((self.first, self.second), results):
                            if error:
                                errors.append(error)
                                continue
                            entries = (entries or [])[:20]
                            numeric = [row["score"] for row in entries
                                       if isinstance(row.get("score"), (int, float))
                                       and not isinstance(row["score"], bool)]
                            lines.append(f"**State {state}** · {len(entries)} entries")
                            if source:
                                lines.append(f"Source: {source}")
                            if numeric:
                                lines.append(f"Highest recorded score: {max(numeric):,.0f}")
                            if len(numeric) == 20 and len(entries) == 20:
                                lines.append(f"Sum of top 20 recorded scores: {sum(numeric):,.0f}")
                            else:
                                lines.append("Full top-20 numeric comparison unavailable")
                        if errors:
                            lines.append("**Ranking unavailable:** " + (
                                errors[0] if len(set(errors)) == 1 else "; ".join(dict.fromkeys(errors))
                            ))
                        else:
                            self.cache[board] = lines
                    except Exception:
                        logger.exception("State comparison failed")
                        lines = ["Unable to fetch rankings. Check Bot Health logs and try again."]
            for option in self.category_selector.options:
                option.default = option.value == board
            await interaction.edit_original_response(embed=self.build_embed(lines), view=self)

    @discord.ui.button(label="Change States", emoji="✏️",
                       style=discord.ButtonStyle.secondary, row=1)
    async def change_states(self, interaction, button):
        await interaction.response.send_modal(
            StateCompareModal(self.cog, self.first, self.second),
        )

    @discord.ui.button(label="Back to Intelligence", emoji="↩️",
                       style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await open_section(self.cog, interaction, "intel")

    @discord.ui.button(label="Main Menu", emoji="🏠",
                       style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction, button):
        await self.cog.show_main_menu(interaction)


class AllianceIntelModal(discord.ui.Modal):
    state_id = discord.ui.TextInput(label="State number", placeholder="Enter state number", max_length=8)
    tag = discord.ui.TextInput(label="Alliance tag", placeholder="Enter alliance tag", max_length=30)

    def __init__(self, cog):
        super().__init__(title="Alliance Intelligence")
        self.cog = cog

    async def on_submit(self, interaction):
        state = str(self.state_id.value).strip()
        tag = str(self.tag.value).strip()
        if not state.isdigit() or int(state) < 1 or not tag:
            await interaction.response.send_message("Enter a state number and alliance tag.", ephemeral=True)
            return
        await invoke_slash(self.cog, interaction, "WosAllianceIntel", "wosalliance",
                           state_id=int(state), tag=tag)


class MainMenuView(discord.ui.View):
    """The five-category entry point, with stable IDs for persistent registration."""
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        for i, (code, (label, emoji, _)) in enumerate(SECTIONS.items()):
            button = discord.ui.Button(label=label, emoji=emoji,
                                       style=discord.ButtonStyle.primary if i == 0 else discord.ButtonStyle.secondary,
                                       custom_id=f"wos_center_{code}", row=i)
            async def clicked(interaction, section=code):
                await open_section(self.cog, interaction, section)
            button.callback = clicked
            self.add_item(button)

    async def interaction_check(self, interaction):
        is_admin, _ = PermissionManager.is_admin(interaction.user.id)
        if not is_admin:
            await interaction.response.send_message("Administrator permission required.", ephemeral=True)
        return is_admin


async def open_section(cog, interaction, section):
    if section not in SECTIONS:
        await missing(interaction, "Navigation")
        return
    title, emoji, items = SECTIONS[section]
    embed = discord.Embed(
        title=f"{emoji} {title}",
        description="Choose a tool from the dropdown. Its description appears in the selection list.\nUse **Back to Home** to switch centers.",
                          color=theme.emColor1)
    await safe_edit_message(interaction, embed=embed,
                            view=CenterSectionView(cog, interaction.user.id, section), content=None)


class CenterSectionView(discord.ui.View):
    def __init__(self, cog, user_id, section):
        super().__init__(timeout=menu_timeout())
        self.cog, self.user_id, self.section = cog, user_id, section
        choices = [
            discord.SelectOption(label=name, value=key, description=desc[:100])
            for key, name, desc in SECTIONS[section][2]
        ]
        selector = discord.ui.Select(placeholder="Choose a tool", options=choices, row=0)
        self.tool_selector = selector
        selector.callback = self.select_tool
        self.add_item(selector)

    async def interaction_check(self, interaction):
        return await authorized(interaction, self.user_id)

    async def select_tool(self, interaction):
        try:
            await route(self.cog, interaction, self.section, self.tool_selector.values[0])
        except Exception:
            logger.exception("Command Center dropdown failed: %s", self.section)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Unable to open this tool. Check Bot Health logs.", ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Unable to open this tool. Check Bot Health logs.", ephemeral=True,
                )

    async def on_error(self, interaction, error, item):
        logger.error("Command Center section interaction failed: %s", self.section,
                     exc_info=(type(error), error, error.__traceback__))
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Menu action failed. Check Bot Health logs.", ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "Menu action failed. Check Bot Health logs.", ephemeral=True,
            )

    @discord.ui.button(label="Back to Home", emoji="🏠", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction, button):
        await self.cog.show_main_menu(interaction)


class AllianceWorkspaceView(discord.ui.View):
    """One selected alliance; use the original write paths and permission checks."""
    def __init__(self, cog, user_id, alliances, alliance_id):
        super().__init__(timeout=menu_timeout())
        self.cog, self.user_id = cog, user_id
        self.alliances = alliances
        self.alliance_id = int(alliance_id)
        selector = discord.ui.Select(
            placeholder="Select alliance",
            options=[discord.SelectOption(label=str(name)[:95], value=str(aid),
                                          default=int(aid) == self.alliance_id)
                     for aid, name in alliances[:25]],
            row=0,
        )
        self.alliance_selector = selector
        selector.callback = self.switch_alliance
        self.add_item(selector)
        # Overview stays focused on summary and alliance context; each operation
        # already has a single dedicated entry in Alliance Command Center.
        redundant = {"Members & Imports", "Troop Quality", "Export Members",
                     "Rankings & History"}
        for item in tuple(self.children):
            if isinstance(item, discord.ui.Button) and item.label in redundant:
                self.remove_item(item)

    async def interaction_check(self, interaction):
        if not await authorized(interaction, self.user_id):
            return False
        permitted, _ = PermissionManager.get_admin_alliances(interaction.user.id, interaction.guild_id)
        if int(self.alliance_id) not in {int(aid) for aid, _ in permitted}:
            await interaction.response.send_message("You no longer have access to this alliance.", ephemeral=True)
            return False
        return True

    async def switch_alliance(self, interaction):
        selected = int(self.alliance_selector.values[0])
        await show_alliance_workspace(self.cog, interaction, selected)

    async def on_error(self, interaction, error, item):
        logger.error("Alliance workspace interaction failed: %s", self.alliance_id,
                     exc_info=(type(error), error, error.__traceback__))
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Alliance menu action failed. Check Bot Health logs.", ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "Alliance menu action failed. Check Bot Health logs.", ephemeral=True,
            )

    @discord.ui.button(label="Members & Imports", emoji="👥", style=discord.ButtonStyle.primary, row=1)
    async def members(self, interaction, button):
        await open_cog(self.cog, interaction, "AllianceMemberOperations",
                       "show_manage_members_for", self.alliance_id)

    @discord.ui.button(label="Troop Quality", emoji="🛡️", style=discord.ButtonStyle.primary, row=1)
    async def troops(self, interaction, button):
        await open_cog(self.cog, interaction, "TroopQuality",
                       "show_troop_quality_for", self.alliance_id)

    @discord.ui.button(label="Alliance Configuration", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2)
    async def configuration(self, interaction, button):
        await self.cog.show_alliance_settings(interaction, self.alliance_id)

    @discord.ui.button(label="Export Members", emoji="📤", style=discord.ButtonStyle.secondary, row=2)
    async def export(self, interaction, button):
        await open_cog(self.cog, interaction, "AllianceMemberOperations",
                       "show_export_members")

    @discord.ui.button(label="Rankings & History", emoji="📊", style=discord.ButtonStyle.secondary, row=3)
    async def history(self, interaction, button):
        await show_alliance_insights(self.cog, interaction, self.alliance_id)

    @discord.ui.button(label="Manage Alliances", emoji="🗂️", style=discord.ButtonStyle.secondary, row=3)
    async def manage_alliances(self, interaction, button):
        """Keep alliance creation and bulk actions accessible."""
        await self.cog.show_alliance_admin_tools(interaction)

    @discord.ui.button(label="Back to Alliances", emoji="↩️", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction, button):
        await open_section(self.cog, interaction, "alliance")

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.secondary, row=4)
    async def home(self, interaction, button):
        await self.cog.show_main_menu(interaction)


def alliance_summary(alliance_id):
    """Lightweight aggregate, no API request or DB mutation."""
    with sqlite3.connect("db/users.sqlite", timeout=30) as db:
        row = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(power), 0), "
            "SUM(CASE WHEN nickname IS NULL OR nickname = '' THEN 1 ELSE 0 END) "
            "FROM users WHERE alliance = ?", (alliance_id,),
        ).fetchone()
        try:
            complete = db.execute(
                "SELECT COUNT(*) FROM users u "
                "JOIN member_troop_quality t ON t.fid=u.fid "
                "WHERE u.alliance=? AND t.infantry_tier IS NOT NULL "
                "AND t.lancer_tier IS NOT NULL AND t.marksman_tier IS NOT NULL",
                (alliance_id,),
            ).fetchone()[0]
        except sqlite3.OperationalError:
            complete = None
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), complete


async def show_alliance_workspace(cog, interaction, alliance_id=None):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Alliance management is available in an installed Discord server.", ephemeral=True)
        return
    alliances, _ = PermissionManager.get_admin_alliances(interaction.user.id,
                                                         interaction.guild_id)
    if not alliances:
        await interaction.response.send_message(
            "No alliances are assigned to your administrator account.", ephemeral=True)
        return
    allowed = {int(aid): name for aid, name in alliances}
    alliance_id = int(alliance_id) if alliance_id is not None else int(alliances[0][0])
    if alliance_id not in allowed:
        await interaction.response.send_message("This alliance is outside your permissions.", ephemeral=True)
        return
    count, power, unnamed, troop_complete = await asyncio.to_thread(alliance_summary, alliance_id)
    embed = discord.Embed(
        title=f"🛡️ Alliance Command Center · {allowed[alliance_id]}",
        description=(
            f"**Registered members:** {count:,}\n"
            f"**Combined recorded power:** {power:,}\n"
            f"**Missing member names:** {unnamed:,}\n"
            f"**Troop coverage:** {troop_complete}/{count} fully recorded\n\n"
            if troop_complete is not None else
            f"**Registered members:** {count:,}\n"
            f"**Combined recorded power:** {power:,}\n"
            f"**Missing member names:** {unnamed:,}\n"
            "**Troop coverage:** Not yet recorded\n\n"
        ) + (
            "Use the alliance selector to view its recorded statistics. "
            "For members, imports, exports, troop quality, rankings and history, "
            "use their dedicated entries in Alliance Command Center.\n\n"
            "Attendance and OCR are under **Events & Scheduling**."
        ),
        color=theme.emColor1,
    )
    await safe_edit_message(interaction, embed=embed,
                            view=AllianceWorkspaceView(cog, interaction.user.id,
                                                       alliances, alliance_id),
                            content=None)


async def show_alliance_settings_entry(cog, interaction):
    """Directly open the existing configuration-only view for an authorized alliance."""
    return await open_alliance_scoped_tool(
        cog, interaction, "MainMenu", "show_alliance_settings", "Alliance Configuration")


class AllianceInsightsView(discord.ui.View):
    """Two related read-only features, with one scoped alliance context."""
    def __init__(self, cog, user_id, alliance_id):
        super().__init__(timeout=menu_timeout())
        self.cog, self.user_id, self.alliance_id = cog, user_id, alliance_id

    async def interaction_check(self, interaction):
        if not await authorized(interaction, self.user_id):
            return False
        permitted, _ = PermissionManager.get_admin_alliances(interaction.user.id, interaction.guild_id)
        if self.alliance_id not in {int(aid) for aid, _ in permitted}:
            await interaction.response.send_message("Alliance access denied.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Alliance History", emoji="📜", style=discord.ButtonStyle.primary)
    async def history(self, interaction, button):
        await open_cog(self.cog, interaction, "AllianceHistory", "show_history_for", self.alliance_id)

    @discord.ui.button(label="Power Rankings", emoji="📊", style=discord.ButtonStyle.primary)
    async def rankings(self, interaction, button):
        await open_cog(self.cog, interaction, "AllianceMemberOperations",
                       "show_power_rankings_for", self.alliance_id)

    @discord.ui.button(label="Back to Alliances", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await open_section(self.cog, interaction, "alliance")

    @discord.ui.button(label="Main Menu", emoji="🏠", style=discord.ButtonStyle.secondary)
    async def home(self, interaction, button):
        await self.cog.show_main_menu(interaction)


async def show_alliance_insights(cog, interaction, alliance_id):
    embed = discord.Embed(
        title="Alliance Rankings & History",
        description="Choose recorded alliance changes or member power rankings.",
        color=theme.emColor1,
    )
    await safe_edit_message(interaction, embed=embed,
                            view=AllianceInsightsView(cog, interaction.user.id, alliance_id),
                            content=None)




class CoordinatesLookupModal(discord.ui.Modal):
    """Coordinates reported by Whiteout Stats, not inferred live positions."""
    chief_id = discord.ui.TextInput(label="Chief ID", placeholder="Enter Chief ID", max_length=24)

    def __init__(self, cog):
        super().__init__(title="Map & Coordinates")
        self.cog = cog

    async def on_submit(self, interaction):
        fid = str(self.chief_id.value).strip()
        if not fid.isascii() or not fid.isdecimal() or not 1 <= len(fid) <= 16 or int(fid) < 1:
            await interaction.response.send_message("Enter a valid numeric Chief ID.", ephemeral=True)
            return
        client = self.cog.bot.get_cog("ChiefStalker")
        if client is None:
            return await missing(interaction, "Player coordinates")
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            from .chief_stalker import basic_fields, freshness
            payload, error = await client.get_player(int(fid))
            if error:
                return await send_lookup_result(self.cog, interaction, content=error)
            data = basic_fields(payload)
            if not data:
                return await send_lookup_result(self.cog, interaction, content="No player data was reported.")
            embed = discord.Embed(
                title="📍 Last reported coordinates",
                description=f"Chief ID: `{fid}`",
                color=theme.emColor1,
            )
            if data.get("x") is None or data.get("y") is None:
                embed.add_field(name="Location", value="Coordinates not reported by the API.")
            else:
                embed.add_field(name="X", value=str(data["x"]), inline=True)
                embed.add_field(name="Y", value=str(data["y"]), inline=True)
            embed.add_field(name="State", value=str(data["state"]) if data.get("state") is not None else "Not reported")
            embed.set_footer(text=f"Whiteout Stats · {freshness(payload)} · Snapshot, not live location")
            await send_lookup_result(self.cog, interaction, embed=embed)
        except Exception:
            logger.exception("Command Center coordinates lookup failed")
            await send_lookup_result(self.cog, interaction, content="Unable to retrieve coordinates. Check Bot Health logs.")


class AllianceHistoryPickView(discord.ui.View):
    """Pick a permitted alliance for the actual member-history menu."""
    def __init__(self, cog, user_id, alliances):
        super().__init__(timeout=menu_timeout())
        self.cog, self.user_id = cog, user_id
        select = discord.ui.Select(
            placeholder="Select alliance history",
            options=[discord.SelectOption(label=str(name)[:95], value=str(aid))
                     for aid, name in alliances[:25]],
        )
        self.selector = select
        select.callback = self.select_alliance
        self.add_item(select)

    async def interaction_check(self, interaction):
        return await authorized(interaction, self.user_id)

    @discord.ui.button(label="Back to Alliances", emoji="↩️",
                       style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await open_section(self.cog, interaction, "alliance")

    @discord.ui.button(label="Main Menu", emoji="🏠",
                       style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction, button):
        await self.cog.show_main_menu(interaction)

    async def select_alliance(self, interaction):
        permitted, _ = PermissionManager.get_admin_alliances(interaction.user.id, interaction.guild_id)
        aid = int(self.selector.values[0])
        if aid not in {int(row[0]) for row in permitted}:
            return await interaction.response.send_message("Alliance access denied.", ephemeral=True)
        await open_cog(self.cog, interaction, "AllianceHistory", "show_history_for", aid)


async def show_alliance_history_entry(cog, interaction):
    if interaction.guild_id is None:
        return await interaction.response.send_message("Alliance history requires a Discord server.", ephemeral=True)
    alliances, _ = PermissionManager.get_admin_alliances(interaction.user.id, interaction.guild_id)
    if not alliances:
        return await interaction.response.send_message("No assigned alliances.", ephemeral=True)
    if len(alliances) == 1:
        return await open_cog(cog, interaction, "AllianceHistory", "show_history_for", int(alliances[0][0]))
    embed = discord.Embed(title="Alliance History", description="Choose an alliance to inspect its member history.",
                          color=theme.emColor1)
    await safe_edit_message(interaction, embed=embed,
                            view=AllianceHistoryPickView(cog, interaction.user.id, alliances), content=None)



class AllianceActionPickView(discord.ui.View):
    """Select an authorized alliance for an existing scoped workflow."""
    def __init__(self, cog, user_id, alliances, target_cog, target_method, label):
        super().__init__(timeout=menu_timeout())
        self.cog, self.user_id = cog, user_id
        self.target_cog, self.target_method = target_cog, target_method
        self.parent_section = "events" if target_cog == "AttendanceReport" else "alliance"
        selector = discord.ui.Select(
            placeholder=label,
            options=[discord.SelectOption(label=str(name)[:95], value=str(aid))
                     for aid, name in alliances[:25]],
        )
        self.selector = selector
        selector.callback = self.select_alliance
        self.add_item(selector)

    async def interaction_check(self, interaction):
        return await authorized(interaction, self.user_id)

    @discord.ui.button(label="Back to Center", emoji="↩️",
                       style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await open_section(self.cog, interaction, self.parent_section)

    @discord.ui.button(label="Main Menu", emoji="🏠",
                       style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction, button):
        await self.cog.show_main_menu(interaction)

    async def select_alliance(self, interaction):
        permitted, _ = PermissionManager.get_admin_alliances(interaction.user.id, interaction.guild_id)
        aid = int(self.selector.values[0])
        if aid not in {int(row[0]) for row in permitted}:
            return await interaction.response.send_message("Alliance access denied.", ephemeral=True)
        await open_cog(self.cog, interaction, self.target_cog, self.target_method, aid)


async def open_alliance_scoped_tool(cog, interaction, target_cog, target_method, title):
    if interaction.guild_id is None:
        return await interaction.response.send_message("This tool requires a Discord server.", ephemeral=True)
    alliances, _ = PermissionManager.get_admin_alliances(interaction.user.id, interaction.guild_id)
    if not alliances:
        return await interaction.response.send_message("No assigned alliances.", ephemeral=True)
    if len(alliances) == 1:
        return await open_cog(cog, interaction, target_cog, target_method, int(alliances[0][0]))
    embed = discord.Embed(title=title, description="Choose the alliance to continue.", color=theme.emColor1)
    await safe_edit_message(
        interaction, embed=embed,
        view=AllianceActionPickView(cog, interaction.user.id, alliances,
                                    target_cog, target_method, title),
        content=None,
    )


async def show_event_reports_entry(cog, interaction):
    return await open_alliance_scoped_tool(
        cog, interaction, "AttendanceReport", "show_session_selection", "Event Reports")



class ImportExportView(discord.ui.View):
    def __init__(self, cog, user_id):
        super().__init__(timeout=menu_timeout())
        self.cog, self.user_id = cog, user_id

    async def interaction_check(self, interaction):
        return await authorized(interaction, self.user_id)

    @discord.ui.button(label="Import / Manage Members", emoji="📥", style=discord.ButtonStyle.primary)
    async def imports(self, interaction, button):
        await open_alliance_scoped_tool(self.cog, interaction,
                                       "AllianceMemberOperations", "show_manage_members_for",
                                       "Import / Manage Members")

    @discord.ui.button(label="Export Members", emoji="📤", style=discord.ButtonStyle.secondary)
    async def exports(self, interaction, button):
        await open_cog(self.cog, interaction, "AllianceMemberOperations", "show_export_members")

    @discord.ui.button(label="Back to Alliances", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await open_section(self.cog, interaction, "alliance")

    @discord.ui.button(label="Main Menu", emoji="🏠", style=discord.ButtonStyle.secondary)
    async def home(self, interaction, button):
        await self.cog.show_main_menu(interaction)


async def show_import_export_entry(cog, interaction):
    embed = discord.Embed(title="Imports & Exports",
                          description="Import via **Import / Manage Members** → Import (CSV/XLSX), or export directly.",
                          color=theme.emColor1)
    await safe_edit_message(interaction, embed=embed,
                            view=ImportExportView(cog, interaction.user.id), content=None)



class GiftCodeManagementView(discord.ui.View):
    """Dedicated code CRUD navigation, without another duplicate full dashboard."""
    def __init__(self, cog, user_id):
        super().__init__(timeout=menu_timeout())
        self.cog, self.user_id = cog, user_id

    async def interaction_check(self, interaction):
        return await authorized(interaction, self.user_id)

    @discord.ui.button(label="Add Gift Code", emoji="➕", style=discord.ButtonStyle.success)
    async def add(self, interaction, button):
        await open_cog(self.cog, interaction, "GiftOperations", "create_gift_code")

    @discord.ui.button(label="List Gift Codes", emoji="📋", style=discord.ButtonStyle.primary)
    async def list_codes(self, interaction, button):
        await open_cog(self.cog, interaction, "GiftOperations", "list_gift_codes")

    @discord.ui.button(label="Delete Gift Code", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction, button):
        await open_cog(self.cog, interaction, "GiftOperations", "delete_gift_code")

    @discord.ui.button(label="Back to Gift Center", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await open_section(self.cog, interaction, "gifts")

    @discord.ui.button(label="Main Menu", emoji="🏠", style=discord.ButtonStyle.secondary)
    async def home(self, interaction, button):
        await self.cog.show_main_menu(interaction)


async def show_gift_code_management(cog, interaction):
    embed = discord.Embed(
        title="Manage Gift Codes",
        description="Add, list or delete codes. Redemption, results, automation "
                    "and channel management have their own Gift Code Center entries.",
        color=theme.emColor1,
    )
    await safe_edit_message(interaction, embed=embed,
                            view=GiftCodeManagementView(cog, interaction.user.id), content=None)


async def open_gift_redemption(cog, interaction):
    """Launch the existing protected GiftView redemption workflow."""
    gift = cog.bot.get_cog("GiftOperations")
    if gift is None:
        return await missing(interaction, "Gift redemption")
    from .gift_views import GiftView
    view = GiftView(gift, interaction.user.id)
    await view.use_gift_alliance_button.callback(interaction)




class AppearanceView(discord.ui.View):
    """Expose both appearance settings previously hidden behind different menus."""
    def __init__(self, cog, user_id):
        super().__init__(timeout=menu_timeout())
        self.cog, self.user_id = cog, user_id

    async def interaction_check(self, interaction):
        return await authorized(interaction, self.user_id)

    @discord.ui.button(label="Themes & Colors", emoji="🎨", style=discord.ButtonStyle.primary)
    async def themes(self, interaction, button):
        await open_cog(self.cog, interaction, "Theme", "show_theme_menu")

    @discord.ui.button(label="Bot Profile & Name Style", emoji="🤖", style=discord.ButtonStyle.secondary)
    async def profile(self, interaction, button):
        await open_cog(self.cog, interaction, "BotProfile", "show_profile")

    @discord.ui.button(label="Back to Administration", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await open_section(self.cog, interaction, "admin")

    @discord.ui.button(label="Main Menu", emoji="🏠", style=discord.ButtonStyle.secondary)
    async def home(self, interaction, button):
        await self.cog.show_main_menu(interaction)


async def show_appearance_entry(cog, interaction):
    embed = discord.Embed(
        title="Appearance & Bot Profile",
        description="Customize the theme, colors, bot avatar, profile, or display-name style.",
        color=theme.emColor1,
    )
    await safe_edit_message(interaction, embed=embed,
                            view=AppearanceView(cog, interaction.user.id), content=None)



async def route(cog, interaction, section, key):
    """Every menu entry routes to a real existing workflow or a truthful alternative."""
    if section == "alliance":
        if key == "overview":
            return await show_alliance_workspace(cog, interaction)
        if key == "members":
            return await open_alliance_scoped_tool(
                cog, interaction, "AllianceMemberOperations", "show_manage_members_for",
                "Members & Imports")
        if key == "troops":
            return await open_alliance_scoped_tool(
                cog, interaction, "TroopQuality", "show_troop_quality_for", "Troop Quality")
        if key == "imports":
            return await open_cog(cog, interaction, "AllianceMemberOperations", "show_export_members")
        if key == "history":
            return await open_alliance_scoped_tool(
                cog, interaction, "AllianceInsights", "show_alliance_insights", "Rankings & History")
        if key == "management":
            return await cog.show_alliance_admin_tools(interaction)
        if key == "settings":
            return await show_alliance_settings_entry(cog, interaction)
        return await open_section(cog, interaction, "alliance")

    if section == "intel":
        if key == "coordinates":
            return await interaction.response.send_modal(CoordinatesLookupModal(cog))
        if key in ("player", "playerhistory"):
            return await interaction.response.send_modal(
                ChiefLookupModal(cog, "playerhistory" if key == "playerhistory" else "player"))
        if key in ("scout", "svs"):
            return await interaction.response.send_modal(StateLookupModal(cog, key))
        if key == "statecompare":
            return await interaction.response.send_modal(StateCompareModal(cog))
        if key == "allianceintel":
            return await interaction.response.send_modal(AllianceIntelModal(cog))

    if section == "events":
        if key == "calendar":
            return await open_cog(cog, interaction, "NotificationSchedule", "show_main_menu")
        if key == "notifications":
            return await open_cog(cog, interaction, "NotificationSystem", "show_notification_menu")
        if key == "bear":
            return await open_cog(cog, interaction, "BearTrack", "show_bear_track_menu")
        if key == "minister":
            return await open_cog(cog, interaction, "MinisterMenu", "show_minister_channel_menu")
        if key == "reports":
            return await show_event_reports_entry(cog, interaction)
        return await open_cog(cog, interaction, "Attendance", "show_attendance_menu")

    if section == "gifts":
        if key == "dashboard":
            return await show_gift_code_management(cog, interaction)
        dispatch = {
            "results": "show_redeem_results",
            "automation": "show_settings_menu",
            "channels": "manage_channel_settings",
        }
        if key == "redeem":
            return await open_gift_redemption(cog, interaction)
        return await open_cog(cog, interaction, "GiftOperations",
                              dispatch.get(key, "show_gift_menu"))

    if section == "admin":
        if key == "permissions":
            return await cog.show_permissions(interaction)
        if key == "sync":
            return await open_cog(cog, interaction, "AllianceApiSync", "show_player_sync")
        if key == "appearance":
            return await show_appearance_entry(cog, interaction)
        if key == "health":
            return await open_cog(cog, interaction, "BotHealth", "show_health_menu")
        if key == "backup":
            return await open_cog(cog, interaction, "BackupOperations", "show_backup_menu")
        if key == "updates":
            return await cog.show_maintenance_tools(interaction)
        if key == "servers":
            return await open_cog(cog, interaction, "BotHealth", "show_servers")
        return await open_section(cog, interaction, "admin")

    await missing(interaction, "Requested module")
