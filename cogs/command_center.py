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
        ("overview", "Alliance Overview", "Statistics and alliance actions"),
        ("members", "Manage Members", "Add, edit, transfer or remove members"),
        ("troops", "Troop Quality", "View, edit and upload troop tiers"),
        ("attendance", "Attendance & OCR", "Screenshots, events and corrections"),
        ("imports", "Imports & Exports", "Upload files and export member records"),
        ("settings", "Alliance Settings", "State, channels and membership rules"),
        ("history", "Alliance History", "Changes, rankings and member history"),
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
        ("calendar", "Event Calendar", "Reminders and upcoming schedules"),
        ("notifications", "Notifications", "Configure notifications and channels"),
        ("bear", "Bear Tracking", "Damage, screenshots and history"),
        ("minister", "Minister Scheduling", "Appointments and archives"),
        ("attendance", "Attendance Management", "Record and correct participation"),
        ("reports", "Event Reports", "Recorded attendance reports and exports"),
    )),
    "gifts": ("Gift Code Center", "🎁", (
        ("dashboard", "Gift Code Dashboard", "Codes, status and controls"),
        ("redeem", "Redeem Gift Codes", "Run the existing redemption flow"),
        ("results", "Redemption Results", "View completed and pending redemptions"),
        ("automation", "Automation Settings", "Validation and retry preferences"),
        ("channels", "Gift Channels", "Announcement and redemption logs"),
    )),
    "admin": ("Administration & Settings", "⚙️", (
        ("permissions", "Permission Management", "Owners, roles and alliance scope"),
        ("sync", "Player Sync & API", "Connection, limits and scheduled sync"),
        ("appearance", "Appearance & Bot Profile", "Themes and customization"),
        ("health", "Bot Health", "Monitoring and running operations"),
        ("backup", "Backup & Restore", "Backup and restore utilities"),
        ("updates", "Updates & Maintenance", "Versions, maintenance and support"),
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


class ChiefLookupModal(discord.ui.Modal):
    chief_id = discord.ui.TextInput(label="Chief ID", placeholder="137940156", max_length=24)

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
    state_id = discord.ui.TextInput(label="State number", placeholder="1501", max_length=8)

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
        await invoke_slash(self.cog, interaction, target, key, state=int(state))


class StateCompareModal(discord.ui.Modal):
    """Top-20 comparison for one supported category; no inferred data."""
    first = discord.ui.TextInput(label="First state", placeholder="1501", max_length=8)
    second = discord.ui.TextInput(label="Second state", placeholder="1502", max_length=8)
    board = discord.ui.TextInput(label="Ranking category", default="personal_power",
                                 placeholder="personal_power / pet_power", max_length=40)

    def __init__(self, cog):
        super().__init__(title="Compare State Rankings")
        self.cog = cog

    async def on_submit(self, interaction):
        from .wos_leaderboards import SCOUT_BOARDS, scout_label
        one, two = str(self.first.value).strip(), str(self.second.value).strip()
        board = str(self.board.value).strip().lower().replace(" ", "_")
        if not one.isdigit() or not two.isdigit() or min(int(one), int(two)) < 1:
            await interaction.response.send_message("Enter two positive state numbers.", ephemeral=True)
            return
        if board not in SCOUT_BOARDS:
            await interaction.response.send_message(
                "Unsupported category. Try personal_power, pet_power or alliance_power.",
                ephemeral=True,
            )
            return
        client = self.cog.bot.get_cog("WosLeaderboards")
        if not client:
            await missing(interaction, "Whiteout Stats leaderboards")
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        left, right = await asyncio.gather(
            client.fetch_board(int(one), board, 20),
            client.fetch_board(int(two), board, 20),
        )
        lines = []
        for sid, (entries, error) in ((one, left), (two, right)):
            if error:
                lines.append(f"**State {sid}:** {error}")
                continue
            numeric = [
                row["score"] for row in entries[:20]
                if isinstance(row.get("score"), (int, float))
                and not isinstance(row["score"], bool)
            ]
            lines.append(f"**State {sid}** · {len(entries[:20])} entries")
            if numeric:
                lines.append(f"Highest recorded score: {max(numeric):,.0f}")
            if len(numeric) == 20 and len(entries) >= 20:
                lines.append(f"Sum of top 20 recorded scores: {sum(numeric):,.0f}")
            else:
                lines.append("Full top-20 numeric comparison unavailable")
        embed = discord.Embed(
            title=f"State {one} vs State {two} · {scout_label(board)}",
            description="\n".join(lines) or "No comparable data returned.",
            color=theme.emColor1,
        )
        embed.set_footer(text="Whiteout Stats snapshots · compare identical ranking categories")
        await interaction.followup.send(embed=embed, ephemeral=True)


class AllianceIntelModal(discord.ui.Modal):
    state_id = discord.ui.TextInput(label="State number", placeholder="1501", max_length=8)
    tag = discord.ui.TextInput(label="Alliance tag", placeholder="VEN", max_length=30)

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
    lines = [f"Select a tool below. All existing editing, upload and export workflows remain available.", ""]
    lines += [f"**{i[1]}** — {i[2]}" for i in items]
    embed = discord.Embed(title=f"{emoji} {title}", description="\n".join(lines),
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
        await route(self.cog, interaction, self.section, self.tool_selector.values[0])

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

    @discord.ui.button(label="Manage Members / Import", emoji="👥", style=discord.ButtonStyle.primary, row=1)
    async def members(self, interaction, button):
        await open_cog(self.cog, interaction, "AllianceMemberOperations",
                       "show_manage_members_for", self.alliance_id)

    @discord.ui.button(label="Troop Quality", emoji="🛡️", style=discord.ButtonStyle.primary, row=1)
    async def troops(self, interaction, button):
        await open_cog(self.cog, interaction, "TroopQuality",
                       "show_troop_quality_for", self.alliance_id)

    @discord.ui.button(label="Attendance & OCR", emoji="📋", style=discord.ButtonStyle.primary, row=2)
    async def attendance(self, interaction, button):
        await open_cog(self.cog, interaction, "Attendance",
                       "show_attendance_hub", self.alliance_id)

    @discord.ui.button(label="Export Members", emoji="📤", style=discord.ButtonStyle.secondary, row=2)
    async def export(self, interaction, button):
        await open_cog(self.cog, interaction, "AllianceMemberOperations",
                       "show_export_members")

    @discord.ui.button(label="Alliance Settings / History", emoji="⚙️", style=discord.ButtonStyle.secondary, row=3)
    async def settings(self, interaction, button):
        await self.cog.show_alliance_hub(interaction, self.alliance_id)

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
            "Choose an alliance and an action. Member edits, CSV/XLSX uploads, "
            "troop edits, attendance OCR and exports open the existing protected workflows.\n\n"
            "**Import:** Manage Members → Import (CSV/XLSX).\n"
            "**Edit:** Manage Members → Edit / Bulk Edit.\n"
            "**Troop uploads:** Troop Quality → submission channel."
        ),
        color=theme.emColor1,
    )
    await safe_edit_message(interaction, embed=embed,
                            view=AllianceWorkspaceView(cog, interaction.user.id,
                                                       alliances, alliance_id),
                            content=None)


async def route(cog, interaction, section, key):
    """Every menu entry routes to a real existing workflow or a truthful alternative."""
    if section == "alliance":
        if key == "attendance":
            return await open_cog(cog, interaction, "Attendance", "show_attendance_menu")
        return await show_alliance_workspace(cog, interaction)

    if section == "intel":
        if key in ("player", "playerhistory", "coordinates"):
            return await interaction.response.send_modal(
                ChiefLookupModal(cog, "playerhistory" if key == "playerhistory" else "player"))
        if key in ("scout", "svs"):
            return await interaction.response.send_modal(StateLookupModal(cog, key))
        if key == "statecompare":
            return await interaction.response.send_modal(StateCompareModal(cog))
        if key == "allianceintel":
            return await interaction.response.send_modal(AllianceIntelModal(cog))

    if section == "events":
        if key in ("calendar", "notifications"):
            return await open_cog(cog, interaction, "NotificationSystem", "show_notification_menu")
        if key == "bear":
            return await open_cog(cog, interaction, "BearTrack", "show_bear_track_menu")
        if key == "minister":
            return await open_cog(cog, interaction, "MinisterMenu", "show_minister_channel_menu")
        return await open_cog(cog, interaction, "Attendance", "show_attendance_menu")

    if section == "gifts":
        dispatch = {
            "dashboard": "show_gift_menu",
            "redeem": "show_gift_menu",
            "results": "show_redeem_results",
            "automation": "show_settings_menu",
            "channels": "manage_channel_settings",
        }
        return await open_cog(cog, interaction, "GiftOperations",
                              dispatch.get(key, "show_gift_menu"))

    if section == "admin":
        if key == "permissions":
            return await cog.show_permissions(interaction)
        if key == "sync":
            return await open_cog(cog, interaction, "AllianceApiSync", "show_player_sync")
        if key == "appearance":
            return await open_cog(cog, interaction, "Theme", "show_theme_menu")
        if key == "health":
            return await open_cog(cog, interaction, "BotHealth", "show_health_menu")
        if key in ("backup", "updates"):
            return await cog.show_maintenance(interaction)
        return await cog.show_alliance_management(interaction)

    await missing(interaction, "Requested module")
