"""
Alliance management cog. Handles alliance CRUD, settings, and member listing.
"""
import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
import asyncio
import logging
from contextlib import closing
from .permission_handler import PermissionManager, TIER_OWNER, TIER_GLOBAL, TIER_SERVER
from . import alliance_identity, alliance_roster_import
from .pimp_my_bot import theme, safe_edit_message, menu_timeout, confirm_timeout

logger = logging.getLogger('alliance')


# Fail-closed denial when the gate can't read the lock (DB busy).
STATE_CHECK_UNAVAILABLE = (
    "Could not verify the alliance's state lock right now (database busy). "
    "Please try again in a moment."
)


def resolve_alliance_kid(alliance_id: int):
    """Read the alliance's LOCK state -> (ok, locked_kid). `locked_kid` is None unless the
    alliance is explicitly state-locked, so `kid` alone (an auto-bound home state) never
    rejects adds - only a deliberate lock does. ok=False means fail closed (read error)."""
    try:
        with sqlite3.connect("db/alliance.sqlite", timeout=30.0) as conn:
            row = conn.execute(
                "SELECT kid, COALESCE(state_locked, 0) FROM alliance_list WHERE alliance_id = ?",
                (alliance_id,),
            ).fetchone()
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "no such table" in msg or "no such column" in msg:
            return True, None  # pre-migration DB: lock feature absent -> unrestricted
        logger.warning(f"State gate read failed for alliance {alliance_id}: {e}")
        print(f"State gate read failed for alliance {alliance_id}: {e}")
        return False, None
    if not row:
        return True, None
    kid, locked = row
    return True, (kid if locked else None)


def state_lock_reason(alliance_kid, player_kid) -> "str | None":
    """Denial reason for this player kid against a resolved alliance kid, else None.

    The kid may be self-reported and unverified, so the wording never claims to know
    where the player actually is - it only states what the alliance accepts."""
    if alliance_kid is None:
        return None
    try:
        if player_kid is not None and int(player_kid) == int(alliance_kid):
            return None
    except (TypeError, ValueError):
        pass
    return f"This alliance only accepts members from State #{alliance_kid}."


def check_alliance_state(alliance_id: int, player_kid) -> "str | None":
    """Single-add gate: denial reason for one player kid, else None. Bulk adds
    should call resolve_alliance_kid once + state_lock_reason per player."""
    ok, alliance_kid = resolve_alliance_kid(alliance_id)
    if not ok:
        return STATE_CHECK_UNAVAILABLE
    return state_lock_reason(alliance_kid, player_kid)


def rename_alliance(alliance_id: int, name: str) -> None:
    with closing(sqlite3.connect('db/alliance.sqlite', timeout=30.0)) as conn:
        conn.execute("UPDATE alliance_list SET name = ? WHERE alliance_id = ?", (name, alliance_id))
        conn.commit()


class Alliance(commands.Cog):
    def __init__(self, bot, conn):
        self.bot = bot
        self.conn = conn

        self._create_table()
        self._check_and_add_column()
        alliance_identity.migrate()

    def _create_table(self):
        with sqlite3.connect('db/alliance.sqlite', timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alliance_list (
                    alliance_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    discord_server_id INTEGER,
                    kid INTEGER
                )
            """)
            conn.commit()

    def _check_and_add_column(self):
        with sqlite3.connect('db/alliance.sqlite', timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(alliance_list)")
            columns = [info[1] for info in cursor.fetchall()]
            if "discord_server_id" not in columns:
                cursor.execute("ALTER TABLE alliance_list ADD COLUMN discord_server_id INTEGER")
                conn.commit()
            # Per-alliance state lock. NULL = no restriction (legacy behaviour).
            if "kid" not in columns:
                cursor.execute("ALTER TABLE alliance_list ADD COLUMN kid INTEGER")
                conn.commit()

    async def cog_unload(self):
        """Close the database connection when the cog is unloaded."""
        if getattr(self, 'conn', None) is not None:
            try:
                self.conn.close()
            except Exception:
                pass

    async def view_alliances(self, interaction: discord.Interaction):
        
        if interaction.guild is None:
            await interaction.response.send_message(f"{theme.deniedIcon} This command must be used in a server, not in DMs.", ephemeral=True)
            return

        user_id = interaction.user.id
        guild_id = interaction.guild.id

        # Use centralized permission check
        is_admin, is_global = PermissionManager.is_admin(user_id)
        if not is_admin:
            await interaction.response.send_message("You do not have permission to view alliances.", ephemeral=True)
            return

        try:
            with sqlite3.connect('db/alliance.sqlite', timeout=30.0) as conn:
                cursor = conn.cursor()
                if is_global:
                    # Global admin - show all alliances
                    query = """
                        SELECT a.alliance_id, a.name, COALESCE(s.interval, 0) as interval
                        FROM alliance_list a
                        LEFT JOIN alliancesettings s ON a.alliance_id = s.alliance_id
                        ORDER BY a.alliance_id ASC
                    """
                    cursor.execute(query)
                else:
                    # Get alliance IDs using centralized permission manager
                    alliance_ids, _ = PermissionManager.get_admin_alliance_ids(user_id, guild_id)

                    if not alliance_ids:
                        embed = discord.Embed(
                            title="Existing Alliances",
                            description="No alliances found for your permissions.",
                            color=theme.emColor1
                        )
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                        return

                    placeholders = ','.join('?' * len(alliance_ids))
                    query = f"""
                        SELECT a.alliance_id, a.name, COALESCE(s.interval, 0) as interval
                        FROM alliance_list a
                        LEFT JOIN alliancesettings s ON a.alliance_id = s.alliance_id
                        WHERE a.alliance_id IN ({placeholders})
                        ORDER BY a.alliance_id ASC
                    """
                    cursor.execute(query, alliance_ids)

                alliances = cursor.fetchall()

            alliance_list = ""
            for alliance_id, name, interval in alliances:

                with sqlite3.connect('db/users.sqlite', timeout=30.0) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                    member_count = cursor.fetchone()[0]

                alliance_list += f"{theme.allianceIcon} **{alliance_id}: {name}**\n{theme.userIcon} Members: {member_count}\n\n"

            if not alliance_list:
                alliance_list = "No alliances found."

            embed = discord.Embed(
                title="Existing Alliances",
                description=alliance_list,
                color=theme.emColor1
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(
                "An error occurred while fetching alliances.", 
                ephemeral=True
            )

    @app_commands.command(name="settings", description="Open settings menu.")
    async def settings(self, interaction: discord.Interaction):
        """Compatibility alias: all admin entry and initial setup rules live in MainMenu."""
        main_menu = self.bot.get_cog("MainMenu")
        if main_menu is None:
            await interaction.response.send_message(
                "Main Menu module is unavailable.", ephemeral=True,
            )
            return
        await main_menu.open_entry(interaction)

    async def show_alliance_operations(self, interaction: discord.Interaction):
        """Display the Alliance Operations menu (Add/Edit/Delete/View alliances)."""
        try:
            embed = discord.Embed(
                title=f"{theme.allianceIcon} Alliance Operations",
                description=(
                    f"Please select an operation:\n\n"
                    f"**Available Operations**\n"
                    f"{theme.upperDivider}\n"
                    f"{theme.addIcon} **Add Alliance**\n"
                    f"└ Create a new alliance\n\n"
                    f"{theme.editListIcon} **Edit Alliance**\n"
                    f"└ Modify existing alliance settings\n\n"
                    f"{theme.trashIcon} **Delete Alliance**\n"
                    f"└ Remove an existing alliance\n\n"
                    f"{theme.eyesIcon} **View Alliances**\n"
                    f"└ List all available alliances\n"
                    f"{theme.lowerDivider}"
                ),
                color=theme.emColor1
            )

            view = discord.ui.View(timeout=menu_timeout())
            view.add_item(discord.ui.Button(
                label="Add Alliance",
                emoji=theme.addIcon,
                style=discord.ButtonStyle.success,
                custom_id="add_alliance"
            ))
            view.add_item(discord.ui.Button(
                label="Edit Alliance",
                emoji=theme.editListIcon,
                style=discord.ButtonStyle.primary,
                custom_id="edit_alliance"
            ))
            view.add_item(discord.ui.Button(
                label="Delete Alliance",
                emoji=theme.trashIcon,
                style=discord.ButtonStyle.danger,
                custom_id="delete_alliance"
            ))
            view.add_item(discord.ui.Button(
                label="View Alliances",
                emoji=theme.eyesIcon,
                style=discord.ButtonStyle.primary,
                custom_id="view_alliances"
            ))
            view.add_item(discord.ui.Button(
                label="Back",
                emoji=theme.backIcon,
                style=discord.ButtonStyle.secondary,
                custom_id="back_to_alliance_management"
            ))

            await safe_edit_message(interaction, embed=embed, view=view, content=None)

        except Exception as e:
            logger.error(f"Error in show_alliance_operations: {e}")
            print(f"Error in show_alliance_operations: {e}")

    async def show_add_alliance_for(self, interaction: discord.Interaction):
        """Direct entry to Add Alliance flow (no operations sub-menu)."""
        await self.add_alliance(interaction)

    async def show_refresh_roster_for(self, interaction: discord.Interaction, alliance_id: int):
        """Admin-only additive roster import for a verified, server-owned alliance."""
        if not _can_register_alliance(interaction) or not _can_manage_target_alliance(
                interaction, alliance_id):
            return await interaction.response.send_message(
                "Only authorized Server/Global Admins or the Bot Owner can import alliance members.",
                ephemeral=True)
        info = await asyncio.to_thread(alliance_identity.verified_identity, alliance_id)
        if not info or not info[2] or not info[3]:
            return await interaction.response.send_message(
                "Verify this alliance's in-game identity first.", ephemeral=True)
        from .membership_alliance_lookup import ResolvedAlliance
        resolved = ResolvedAlliance(int(info[1]), int(info[2]), info[3], info[4])
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            entries = await alliance_roster_import.fetch_roster(self.bot, resolved)
            report = await asyncio.to_thread(
                alliance_roster_import.import_roster,
                int(info[0]), alliance_id, resolved, entries)
        except (ValueError, sqlite3.Error) as exc:
            return await interaction.followup.send(
                "Alliance roster import failed: " + str(exc), ephemeral=True)
        await interaction.followup.send(_roster_report(report), ephemeral=True)

    async def show_verify_identity_for(self,interaction:discord.Interaction,alliance_id:int):
        if not _can_manage_target_alliance(interaction, alliance_id):
            return await interaction.response.send_message("Access denied.",ephemeral=True)
        info=alliance_identity.verified_identity(alliance_id)
        if not info or info[0] is None:
            return await interaction.response.send_message(
                "Managed alliance not found in this server.",ephemeral=True)
        if info[1] is None:
            return await interaction.response.send_message(
                "Set the alliance home state first, then verify its exact game tag.",
                ephemeral=True)
        await interaction.response.send_modal(
            VerifyExistingAllianceModal(self,alliance_id,info[4],int(info[1])))

    async def show_edit_name_for(self, interaction: discord.Interaction, alliance_id: int):
        """Direct entry: rename a single alliance (no other settings)."""
        with sqlite3.connect('db/alliance.sqlite', timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,)
            )
            row = cursor.fetchone()
        if not row:
            await interaction.response.send_message(
                f"{theme.deniedIcon} Alliance not found.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            EditNameModal(alliance_id, row[0])
        )

    async def show_edit_state_for(self, interaction: discord.Interaction, alliance_id: int):
        """Open the per-alliance state-lock modal. Empty clears the lock."""
        with sqlite3.connect('db/alliance.sqlite', timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name, kid, COALESCE(state_locked, 0) FROM alliance_list WHERE alliance_id = ?",
                (alliance_id,),
            )
            row = cursor.fetchone()
        if not row:
            await interaction.response.send_message(
                f"{theme.deniedIcon} Alliance not found.", ephemeral=True
            )
            return
        alliance_name, kid, locked = row
        current_lock = kid if locked else None
        await interaction.response.send_modal(
            EditStateModal(alliance_id, alliance_name, current_lock, self.conn, self.bot)
        )

    async def show_delete_alliance_for(self, interaction: discord.Interaction, alliance_id: int):
        """Hub-context entry: delete a known alliance (skip the picker)."""
        await self.alliance_delete_callback(interaction, alliance_id=alliance_id)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type == discord.InteractionType.component:
            custom_id = interaction.data.get("custom_id")

            # Only handle custom_ids that belong to this cog
            handled_ids = {
                "alliance_operations", "back_to_alliance_management", "edit_alliance",
                "add_alliance", "delete_alliance", "view_alliances",
            }
            if custom_id not in handled_ids:
                return

            user_id = interaction.user.id
            with sqlite3.connect('db/settings.sqlite', timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, is_initial FROM admin WHERE id = ?", (user_id,))
                admin = cursor.fetchone()

            if admin is None:
                await interaction.response.send_message("You do not have permission to perform this action.", ephemeral=True)
                return

            try:
                if custom_id == "alliance_operations":
                    await self.show_alliance_operations(interaction)

                elif custom_id == "back_to_alliance_management":
                    main_menu_cog = self.bot.get_cog("MainMenu")
                    if main_menu_cog:
                        await main_menu_cog.show_alliance_management(interaction)

                elif custom_id == "edit_alliance":
                    await self.edit_alliance(interaction)

                elif custom_id == "add_alliance":
                    await self.add_alliance(interaction)

                elif custom_id == "delete_alliance":
                    await self.delete_alliance(interaction)

                elif custom_id == "view_alliances":
                    await self.view_alliances(interaction)

                elif custom_id == "main_menu":
                    # Delegate to MainMenu cog
                    main_menu_cog = self.bot.get_cog("MainMenu")
                    if main_menu_cog:
                        await main_menu_cog.show_main_menu(interaction)

            except Exception as e:
                if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                    logger.error(f"Error processing interaction with custom_id '{custom_id}': {e}")
                    print(f"Error processing interaction with custom_id '{custom_id}': {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while processing your request. Please try again.",
                        ephemeral=True
                    )

    async def add_alliance(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Please perform this action in a Discord channel.", ephemeral=True
            )
            return
        await interaction.response.send_modal(AddAllianceModal(self))

    async def edit_alliance(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(f"{theme.deniedIcon} This command must be used in a server.", ephemeral=True)
            return

        user_id = interaction.user.id
        guild_id = interaction.guild.id

        # Get alliances this admin can access
        admin_alliances, is_global = PermissionManager.get_admin_alliances(user_id, guild_id)

        if not admin_alliances:
            no_alliance_embed = discord.Embed(
                title=f"{theme.deniedIcon}No Alliances Found",
                description="You don't have access to any alliances.",
                color=theme.emColor2
            )
            return await interaction.response.send_message(embed=no_alliance_embed, ephemeral=True)

        # Fetch full alliance details for the ones admin can access
        alliance_ids = [a[0] for a in admin_alliances]
        placeholders = ','.join('?' * len(alliance_ids))
        with sqlite3.connect('db/alliance.sqlite', timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT alliance_id, name
                FROM alliance_list
                WHERE alliance_id IN ({placeholders})
                ORDER BY alliance_id ASC
            """, alliance_ids)
            alliances = cursor.fetchall()

        if not alliances:
            no_alliance_embed = discord.Embed(
                title=f"{theme.deniedIcon}No Alliances Found",
                description=(
                    "There are no alliances registered in the database.\n"
                    "Please create an alliance first using the `/alliance create` command."
                ),
                color=theme.emColor2
            )
            no_alliance_embed.set_footer(text="Use /alliance create to add a new alliance")
            return await interaction.response.send_message(embed=no_alliance_embed, ephemeral=True)

        alliance_options = [
            discord.SelectOption(
                label=f"{name} (ID: {alliance_id})",
                value=f"{alliance_id}",
            ) for alliance_id, name in alliances
        ]
        
        items_per_page = 25
        option_pages = [alliance_options[i:i + items_per_page] for i in range(0, len(alliance_options), items_per_page)]
        total_pages = len(option_pages)

        class PaginatedAllianceView(discord.ui.View):
            def __init__(self, pages, original_callback):
                super().__init__(timeout=menu_timeout())
                self.current_page = 0
                self.pages = pages
                self.original_callback = original_callback
                self.total_pages = len(pages)
                self.update_view()

            def update_view(self):
                self.clear_items()
                
                select = discord.ui.Select(
                    placeholder=f"Select alliance ({self.current_page + 1}/{self.total_pages})",
                    options=self.pages[self.current_page]
                )
                select.callback = self.original_callback
                self.add_item(select)
                
                previous_button = discord.ui.Button(
                    label="",
                    emoji=f"{theme.prevIcon}",
                    style=discord.ButtonStyle.grey,
                    custom_id="previous",
                    disabled=(self.current_page == 0)
                )
                previous_button.callback = self.previous_callback
                self.add_item(previous_button)

                next_button = discord.ui.Button(
                    label="",
                    emoji=f"{theme.nextIcon}",
                    style=discord.ButtonStyle.grey,
                    custom_id="next",
                    disabled=(self.current_page == len(self.pages) - 1)
                )
                next_button.callback = self.next_callback
                self.add_item(next_button)

            async def previous_callback(self, interaction: discord.Interaction):
                self.current_page = (self.current_page - 1) % len(self.pages)
                self.update_view()
                
                embed = interaction.message.embeds[0]
                embed.description = (
                    f"**Instructions:**\n"
                    f"{theme.upperDivider}\n"
                    f"{theme.num1Icon} Select an alliance from the dropdown menu\n"
                    f"{theme.num2Icon} Use {theme.prevIcon} {theme.nextIcon} buttons to navigate between pages\n\n"
                    f"**Current Page:** {self.current_page + 1}/{self.total_pages}\n"
                    f"**Total Alliances:** {sum(len(page) for page in self.pages)}\n"
                    f"{theme.lowerDivider}"
                )
                await interaction.response.edit_message(embed=embed, view=self)

            async def next_callback(self, interaction: discord.Interaction):
                self.current_page = (self.current_page + 1) % len(self.pages)
                self.update_view()

                embed = interaction.message.embeds[0]
                embed.description = (
                    f"**Instructions:**\n"
                    f"{theme.upperDivider}\n"
                    f"{theme.num1Icon} Select an alliance from the dropdown menu\n"
                    f"{theme.num2Icon} Use {theme.prevIcon} {theme.nextIcon} buttons to navigate between pages\n\n"
                    f"**Current Page:** {self.current_page + 1}/{self.total_pages}\n"
                    f"**Total Alliances:** {sum(len(page) for page in self.pages)}\n"
                    f"{theme.lowerDivider}"
                )
                await interaction.response.edit_message(embed=embed, view=self)

        async def select_callback(select_interaction: discord.Interaction):
            try:
                alliance_id = int(select_interaction.data["values"][0])
                alliance_data = next(a for a in alliances if a[0] == alliance_id)

                modal = AllianceModal(
                    title="Edit Alliance",
                    default_name=alliance_data[1],
                )
                await select_interaction.response.send_modal(modal)
                await modal.wait()

                try:
                    alliance_name = modal.name.value.strip()
                    await asyncio.to_thread(rename_alliance, alliance_id, alliance_name)

                    result_embed = discord.Embed(
                        title=f"{theme.verifiedIcon} Alliance Successfully Updated",
                        description="The alliance details have been updated as follows:",
                        color=theme.emColor3
                    )
                    info_section = (
                        f"**🛡️ Alliance Name**\n{alliance_name}\n\n"
                        f"**🔢 Alliance ID**\n{alliance_id}"
                    )
                    result_embed.add_field(name="Alliance Details", value=info_section, inline=False)
                    result_embed.set_footer(text="Alliance settings have been successfully saved")
                    result_embed.timestamp = discord.utils.utcnow()
                    await modal.interaction.response.edit_message(embed=result_embed, view=None)

                except Exception as e:
                    error_embed = discord.Embed(
                        title="Error",
                        description=f"Error: {str(e)}",
                        color=theme.emColor2
                    )
                    await modal.interaction.response.send_message(embed=error_embed, ephemeral=True)

            except Exception as e:
                error_embed = discord.Embed(
                    title=f"{theme.deniedIcon}Error",
                    description=f"An error occurred: {str(e)}",
                    color=theme.emColor2
                )
                if not select_interaction.response.is_done():
                    await select_interaction.response.send_message(embed=error_embed, ephemeral=True)
                else:
                    await select_interaction.followup.send(embed=error_embed, ephemeral=True)

        view = PaginatedAllianceView(option_pages, select_callback)
        embed = discord.Embed(
            title=f"{theme.shieldIcon} Alliance Edit Menu",
            description=(
                f"**Instructions:**\n"
                f"{theme.upperDivider}\n"
                f"{theme.num1Icon} Select an alliance from the dropdown menu\n"
                f"{theme.num2Icon} Use {theme.prevIcon} {theme.nextIcon} buttons to navigate between pages\n\n"
                f"**Current Page:** {1}/{total_pages}\n"
                f"**Total Alliances:** {len(alliances)}\n"
                f"{theme.lowerDivider}"
            ),
            color=theme.emColor1
        )
        embed.set_footer(text="Use the dropdown menu below to select an alliance")
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def delete_alliance(self, interaction: discord.Interaction):
        try:
            if interaction.guild is None:
                await interaction.response.send_message(f"{theme.deniedIcon} This command must be used in a server.", ephemeral=True)
                return

            user_id = interaction.user.id
            guild_id = interaction.guild.id

            # Get alliances this admin can access
            admin_alliances, is_global = PermissionManager.get_admin_alliances(user_id, guild_id)

            if not admin_alliances:
                no_alliance_embed = discord.Embed(
                    title=f"{theme.deniedIcon}No Alliances Found",
                    description="You don't have access to any alliances.",
                    color=theme.emColor2
                )
                await interaction.response.send_message(embed=no_alliance_embed, ephemeral=True)
                return

            # Use the alliances from permission manager (already has id, name)
            alliances = admin_alliances

            if not alliances:
                no_alliance_embed = discord.Embed(
                    title=f"{theme.deniedIcon}No Alliances Found",
                    description="There are no alliances to delete.",
                    color=theme.emColor2
                )
                await interaction.response.send_message(embed=no_alliance_embed, ephemeral=True)
                return

            alliance_members = {}
            for alliance_id, _ in alliances:
                with sqlite3.connect('db/users.sqlite', timeout=30.0) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                    member_count = cursor.fetchone()[0]
                alliance_members[alliance_id] = member_count

            items_per_page = 25
            all_options = [
                discord.SelectOption(
                    label=f"{name[:40]} (ID: {alliance_id})",
                    value=f"{alliance_id}",
                    description=f"{theme.membersIcon} Members: {alliance_members[alliance_id]} | Click to delete",
                    emoji=theme.trashIcon
                ) for alliance_id, name in alliances
            ]
            
            option_pages = [all_options[i:i + items_per_page] for i in range(0, len(all_options), items_per_page)]
            
            embed = discord.Embed(
                title=f"{theme.trashIcon} Delete Alliance",
                description=(
                    f"**{theme.warnIcon} Warning: This action cannot be undone!**\n"
                    f"{theme.upperDivider}\n"
                    f"{theme.num1Icon} Select an alliance from the dropdown menu\n"
                    f"{theme.num2Icon} Use {theme.prevIcon} {theme.nextIcon} buttons to navigate between pages\n\n"
                    f"**Current Page:** 1/{len(option_pages)}\n"
                    f"**Total Alliances:** {len(alliances)}\n"
                    f"{theme.lowerDivider}"
                ),
                color=theme.emColor2
            )
            embed.set_footer(text=f"{theme.warnIcon} Warning: Deleting an alliance will remove all its data!")
            embed.timestamp = discord.utils.utcnow()

            view = PaginatedDeleteView(option_pages, self.alliance_delete_callback)
            
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in delete_alliance: {e}")
            print(f"Error in delete_alliance: {e}")
            error_embed = discord.Embed(
                title=f"{theme.deniedIcon}Error",
                description="An error occurred while loading the delete menu.",
                color=theme.emColor2
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)

    async def alliance_delete_callback(self, interaction: discord.Interaction, alliance_id: int | None = None):
        try:
            if alliance_id is None:
                alliance_id = int(interaction.data["values"][0])

            with sqlite3.connect('db/alliance.sqlite', timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                alliance_data = cursor.fetchone()

            if not alliance_data:
                await interaction.response.send_message("Alliance not found.", ephemeral=True)
                return

            alliance_name = alliance_data[0]
            # Never delete an alliance belonging to another Discord server.
            home = alliance_identity.verified_identity(alliance_id)
            if (interaction.guild_id is None or not home or home[0] is None
                    or not _can_manage_target_alliance(interaction, alliance_id)):
                await interaction.response.send_message(
                    "Alliance deletion: access denied for this server.", ephemeral=True)
                return

            with sqlite3.connect('db/alliance.sqlite', timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM alliancesettings WHERE alliance_id = ?", (alliance_id,))
                settings_count = cursor.fetchone()[0]

            with sqlite3.connect('db/users.sqlite', timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                users_count = cursor.fetchone()[0]

            with sqlite3.connect('db/settings.sqlite', timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM adminserver WHERE alliances_id = ?", (alliance_id,))
                admin_server_count = cursor.fetchone()[0]

            with sqlite3.connect('db/giftcode.sqlite', timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM giftcode_channel WHERE alliance_id = ?", (alliance_id,))
                gift_channels_count = cursor.fetchone()[0]

            with sqlite3.connect('db/giftcode.sqlite', timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM giftcodecontrol WHERE alliance_id = ?", (alliance_id,))
                gift_code_control_count = cursor.fetchone()[0]

            with sqlite3.connect('db/settings.sqlite', timeout=30.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM alliance_logs WHERE alliance_id = ?", (alliance_id,))
                alliance_logs_count = cursor.fetchone()[0]

            confirm_embed = discord.Embed(
                title=f"{theme.warnIcon} Confirm Alliance Deletion",
                description=(
                    f"Are you sure you want to delete this alliance?\n\n"
                    f"**Alliance Details:**\n"
                    f"{theme.allianceIcon} **Name:** {alliance_name}\n"
                    f"{theme.levelIcon} **ID:** {alliance_id}\n"
                    f"{theme.membersIcon} **Members:** {users_count}\n\n"
                    f"**Data to be Deleted:**\n"
                    f"{theme.settingsIcon} Alliance Settings: {settings_count}\n"
                    f"{theme.membersIcon} User Records: {users_count}\n"
                    f"{theme.allianceIcon} Admin Server Records: {admin_server_count}\n"
                    f"{theme.announceIcon} Gift Channels: {gift_channels_count}\n"
                    f"{theme.chartIcon} Gift Code Controls: {gift_code_control_count}\n"
                    f"{theme.listIcon} Alliance Logs: {alliance_logs_count}\n\n"
                    f"**{theme.warnIcon} WARNING: This action cannot be undone!**"
                ),
                color=theme.emColor2
            )
            
            confirm_view = discord.ui.View(timeout=confirm_timeout())
            
            async def confirm_callback(button_interaction: discord.Interaction):
                if (button_interaction.user.id != interaction.user.id
                        or button_interaction.guild_id != interaction.guild_id
                        or not _can_manage_target_alliance(button_interaction, alliance_id)):
                    return await button_interaction.response.send_message(
                        "Alliance deletion: permission denied.", ephemeral=True)
                try:
                    # Delete dependents first and the alliance row LAST. The DBs
                    # aren't a single transaction, so if a step fails the alliance
                    # still exists and its members stay valid instead of orphaned.
                    with sqlite3.connect('db/users.sqlite', timeout=30.0) as conn:
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM users WHERE alliance = ?", (alliance_id,))
                        users_count_deleted = cursor.rowcount
                        conn.commit()

                    with sqlite3.connect('db/settings.sqlite', timeout=30.0) as conn:
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM adminserver WHERE alliances_id = ?", (alliance_id,))
                        admin_server_count = cursor.rowcount

                        cursor.execute("DELETE FROM alliance_logs WHERE alliance_id = ?", (alliance_id,))
                        alliance_logs_deleted = cursor.rowcount

                        conn.commit()

                    with sqlite3.connect('db/giftcode.sqlite', timeout=30.0) as conn:
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM giftcode_channel WHERE alliance_id = ?", (alliance_id,))
                        gift_channels_count = cursor.rowcount

                        cursor.execute("DELETE FROM giftcodecontrol WHERE alliance_id = ?", (alliance_id,))
                        gift_code_control_count = cursor.rowcount

                        conn.commit()

                    with closing(sqlite3.connect('db/id_channel.sqlite', timeout=30.0)) as conn, conn:
                        id_channel_rows = conn.execute(
                            "SELECT channel_id, info_message_id FROM id_channels WHERE alliance_id = ?",
                            (alliance_id,)).fetchall()
                        conn.execute("DELETE FROM id_channels WHERE alliance_id = ?", (alliance_id,))

                    with sqlite3.connect('db/alliance.sqlite', timeout=30.0) as conn:
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM alliancesettings WHERE alliance_id = ?", (alliance_id,))
                        admin_settings_count = cursor.rowcount

                        cursor.execute("CREATE TABLE IF NOT EXISTS troop_quality_channels (alliance_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL UNIQUE, updated_at TEXT NOT NULL, updated_by INTEGER NOT NULL)")
                        cursor.execute("DELETE FROM troop_quality_channels WHERE alliance_id = ?", (alliance_id,))
                        cursor.execute("DELETE FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                        alliance_count = cursor.rowcount

                        conn.commit()

                    # Remove movement cases and observations after the alliance row
                    # has been deleted, so no old cases can appear in other servers.
                    from . import membership_service
                    try:
                        await asyncio.to_thread(membership_service.purge_alliance, alliance_id)
                    except Exception:
                        logger.exception("Failed to purge membership tracking for deleted alliance %s", alliance_id)
                        # The next bot startup will retry legacy-orphan cleanup.

                    cleanup_embed = discord.Embed(
                        title=f"{theme.verifiedIcon} Alliance Successfully Deleted",
                        description=(
                            f"Alliance **{alliance_name}** has been deleted.\n\n"
                            "**Cleaned Up Data:**\n"
                            f"{theme.allianceIcon} Alliance Records: {alliance_count}\n"
                            f"{theme.membersIcon} Users Removed: {users_count_deleted}\n"
                            f"{theme.settingsIcon} Alliance Settings: {admin_settings_count}\n"
                            f"{theme.allianceIcon} Admin Server Records: {admin_server_count}\n"
                            f"{theme.announceIcon} Gift Channels: {gift_channels_count}\n"
                            f"{theme.chartIcon} Gift Code Controls: {gift_code_control_count}\n"
                            f"{theme.listIcon} Alliance Logs: {alliance_logs_deleted}"
                        ),
                        color=theme.emColor3
                    )
                    cleanup_embed.set_footer(text="All related data has been successfully removed")
                    cleanup_embed.timestamp = discord.utils.utcnow()
                    
                    cleanup_view = discord.ui.View(timeout=confirm_timeout())
                    back_btn = discord.ui.Button(
                        label="Back to Alliances",
                        emoji=theme.backIcon,
                        style=discord.ButtonStyle.secondary,
                    )

                    async def _back_to_alliances(back_interaction: discord.Interaction):
                        main_menu = self.bot.get_cog("MainMenu")
                        if main_menu:
                            await main_menu.show_alliance_management(back_interaction)
                    back_btn.callback = _back_to_alliances
                    cleanup_view.add_item(back_btn)
                    await button_interaction.response.edit_message(embed=cleanup_embed, view=cleanup_view)

                    id_channel_cog = self.bot.get_cog("AllianceIDChannel")
                    if id_channel_cog:
                        for channel_id, info_message_id in id_channel_rows:
                            try:
                                await id_channel_cog.remove_info_message(channel_id, info_message_id)
                            except Exception as e:
                                logger.warning(f"Could not remove ID channel info message in {channel_id}: {e}")

                except Exception as e:
                    error_embed = discord.Embed(
                        title=f"{theme.deniedIcon}Error",
                        description=f"An error occurred while deleting the alliance: {str(e)}",
                        color=theme.emColor2
                    )
                    error_view = discord.ui.View(timeout=confirm_timeout())
                    err_back = discord.ui.Button(
                        label="Back",
                        emoji=theme.backIcon,
                        style=discord.ButtonStyle.secondary,
                    )

                    async def _back_to_hub(back_interaction: discord.Interaction):
                        main_menu = self.bot.get_cog("MainMenu")
                        if main_menu:
                            await main_menu.show_alliance_hub(back_interaction, alliance_id)
                    err_back.callback = _back_to_hub
                    error_view.add_item(err_back)
                    await button_interaction.response.edit_message(embed=error_embed, view=error_view)

            async def cancel_callback(button_interaction: discord.Interaction):
                # Silent return to the alliance hub — user clicked Cancel,
                # no need for a separate "Cancelled" dead-end screen.
                main_menu = self.bot.get_cog("MainMenu")
                if main_menu:
                    await main_menu.show_alliance_hub(button_interaction, alliance_id)
                else:
                    await button_interaction.response.edit_message(
                        embed=discord.Embed(
                            title=f"{theme.deniedIcon} Deletion Cancelled",
                            description="Alliance deletion has been cancelled.",
                            color=theme.emColor4,
                        ),
                        view=None,
                    )

            confirm_button = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.danger)
            cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.grey)
            confirm_button.callback = confirm_callback
            cancel_button.callback = cancel_callback
            confirm_view.add_item(confirm_button)
            confirm_view.add_item(cancel_button)

            await interaction.response.edit_message(embed=confirm_embed, view=confirm_view)

        except Exception as e:
            logger.error(f"Error in alliance_delete_callback: {e}")
            print(f"Error in alliance_delete_callback: {e}")
            error_embed = discord.Embed(
                title=f"{theme.deniedIcon}Error",
                description="An error occurred while processing the deletion.",
                color=theme.emColor2
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=error_embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=error_embed, ephemeral=True)

class AllianceModal(discord.ui.Modal):
    def __init__(self, title: str, default_name: str = ""):
        super().__init__(title=title)

        self.name = discord.ui.TextInput(
            label="Alliance Name",
            placeholder="Enter alliance name",
            default=default_name,
            required=True
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction):
        self.interaction = interaction


class PostCreateChannelPromptView(discord.ui.View):
    """One-shot prompt after Add Alliance: set up channels now, or skip to the hub."""

    def __init__(self, cog, alliance_id: int, alliance_name: str, roster_summary: str = ""):
        super().__init__(timeout=menu_timeout())
        self.cog = cog
        self.alliance_id = alliance_id
        self.alliance_name = alliance_name
        self.roster_summary = roster_summary

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        allowed = _can_register_alliance(interaction) and _alliance_in_guild(
            self.alliance_id, interaction.guild_id)
        if not allowed:
            await interaction.response.send_message(
                f"{theme.deniedIcon} Server/Global Admins only.", ephemeral=True
            )
        return allowed

    def build_embed(self) -> discord.Embed:
        return discord.Embed(
            title=f"{theme.verifiedIcon} Alliance Created: {self.alliance_name}",
            description=(
                f"{theme.upperDivider}\n" +
                (f"**Roster import:** {self.roster_summary}\n\n" if self.roster_summary else "") +
                f"Set up this alliance's channels now. A Redemption Log is recommended "
                f"so gift code results get logged.\n\n"
                f"**Controls**\n"
                f"{theme.settingsIcon} **Set Up Channels**\n"
                f"└ Configure the ID, Activity Log, Redemption Log, and Sync Log channels\n\n"
                f"{theme.forwardIcon} **Skip for Now**\n"
                f"└ Go to the alliance hub (Channel Setup stays available there)\n"
                f"{theme.lowerDivider}"
            ),
            color=theme.emColor1,
        )

    @discord.ui.button(label="Refresh Roster", style=discord.ButtonStyle.primary)
    async def refresh_roster(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        info = await asyncio.to_thread(alliance_identity.verified_identity, self.alliance_id)
        if not info or not info[2] or not info[3]:
            return await interaction.followup.send("Alliance verification is missing.", ephemeral=True)
        from .membership_alliance_lookup import ResolvedAlliance
        resolved = ResolvedAlliance(int(info[1]), int(info[2]), info[3], info[4])
        try:
            entries = await alliance_roster_import.fetch_roster(self.cog.bot, resolved)
            report = await asyncio.to_thread(
                alliance_roster_import.import_roster, interaction.guild_id,
                self.alliance_id, resolved, entries)
        except (ValueError, sqlite3.Error) as exc:
            return await interaction.followup.send(
                "Roster refresh failed: " + str(exc), ephemeral=True)
        await interaction.followup.send(_roster_report(report), ephemeral=True)

    @discord.ui.button(label="Set Up Channels", emoji=theme.settingsIcon, style=discord.ButtonStyle.primary)
    async def setup_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        channels_cog = self.cog.bot.get_cog("AllianceChannels")
        if not channels_cog:
            await interaction.response.send_message(
                f"{theme.deniedIcon} Channel Setup module not found.", ephemeral=True
            )
            return
        self.stop()
        await channels_cog.show_channel_setup_for(interaction, self.alliance_id)

    @discord.ui.button(label="Skip for Now", emoji=theme.forwardIcon, style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        main_menu = self.cog.bot.get_cog("MainMenu")
        if main_menu:
            await main_menu.show_alliance_hub(interaction, self.alliance_id)


def _alliance_in_guild(alliance_id, guild_id):
    if guild_id is None:
        return False
    with sqlite3.connect("db/alliance.sqlite", timeout=30) as db:
        return db.execute(
            "SELECT 1 FROM alliance_list WHERE alliance_id=? AND discord_server_id=?",
            (alliance_id, guild_id)).fetchone() is not None


def _can_manage_target_alliance(interaction, alliance_id):
    """The registered Bot Owner can manage an alliance from any bot server.

    Non-owners must operate within the alliance's home Discord server and
    retain their existing alliance-level access restrictions.
    """
    if PermissionManager.is_owner(interaction.user.id):
        return alliance_identity.verified_identity(alliance_id) is not None
    return (_alliance_in_guild(alliance_id, interaction.guild_id)
            and PermissionManager.can_access_alliance(
                interaction.user.id, interaction.guild_id, alliance_id))


def _roster_report(report):
    return (f"Roster: {report.received} returned, {report.added} added, "
            f"{report.already_home} already registered here, "
            f"{report.conflicts} kept in their existing managed alliances. "
            "No member was reassigned or removed.")


def _can_register_alliance(interaction):
    if interaction.guild_id is None:
        return False
    return PermissionManager.get_effective_tier(
        interaction.user.id,interaction.guild_id) in (TIER_OWNER,TIER_GLOBAL,TIER_SERVER)


class ConfirmManagedAllianceView(discord.ui.View):
    """Verification is API-backed; callbacks re-check guild, identity and role."""
    def __init__(self,cog,owner,guild_id,resolved,display_name,existing_id=None):
        super().__init__(timeout=confirm_timeout())
        self.cog,self.owner,self.guild_id=cog,owner,guild_id
        self.resolved,self.display_name,self.existing_id=resolved,display_name,existing_id

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner or interaction.guild_id!=self.guild_id:
            await interaction.response.send_message("This verification belongs to another session.",ephemeral=True)
            return False
        if self.existing_id is None:
            authorized=_can_register_alliance(interaction)
        else:
            authorized=_can_register_alliance(interaction) and _can_manage_target_alliance(
                interaction, self.existing_id)
        if not authorized:
            await interaction.response.send_message("Alliance verification: access denied.",ephemeral=True)
        return authorized

    @discord.ui.button(label="Confirm Verified Alliance",style=discord.ButtonStyle.success)
    async def confirm(self,interaction,button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        entries = None
        if self.existing_id is None:
            try:
                entries = await alliance_roster_import.fetch_roster(self.cog.bot, self.resolved)
            except (ValueError, sqlite3.Error) as exc:
                return await interaction.followup.send(
                    "Alliance not created because the roster could not be verified: "
                    + str(exc), ephemeral=True)
        try:
            if self.existing_id is None:
                alliance_id=await asyncio.to_thread(
                    alliance_identity.create_verified,self.display_name,self.guild_id,self.resolved)
                # Match the existing alliance-creation gift configuration.
                with sqlite3.connect('db/giftcode.sqlite',timeout=30.0) as db:
                    db.execute("INSERT INTO giftcodecontrol(alliance_id,status) VALUES (?,1)",
                               (alliance_id,))
                    db.commit()
            else:
                alliance_id=self.existing_id
                home = await asyncio.to_thread(alliance_identity.verified_identity, alliance_id)
                if not home or not _can_manage_target_alliance(interaction, alliance_id):
                    raise ValueError("Alliance management permission changed.")
                await asyncio.to_thread(
                    alliance_identity.save_verified,alliance_id,int(home[0]),self.resolved,
                    self.resolved.state_id)
        except (ValueError,sqlite3.Error) as exc:
            return await interaction.followup.send(
                "Alliance verification was not saved: "+str(exc),ephemeral=True)
        for item in self.children:
            item.disabled=True
        if self.existing_id is None:
            try:
                report = await asyncio.to_thread(
                    alliance_roster_import.import_roster, self.guild_id,
                    alliance_id, self.resolved, entries)
                roster_summary = _roster_report(report)
            except (ValueError, sqlite3.Error) as exc:
                roster_summary = "Import failed: " + str(exc) + ". Use Refresh Roster to retry."
            prompt=PostCreateChannelPromptView(
                self.cog,alliance_id,self.display_name,roster_summary)
            await interaction.followup.send(embed=prompt.build_embed(),view=prompt,ephemeral=True)
        else:
            await interaction.followup.send(
                "Verified **"+discord.utils.escape_markdown(self.display_name)+"** as **["+
                self.resolved.tag+"] "+discord.utils.escape_markdown(self.resolved.name)+
                "** in State "+str(self.resolved.state_id)+
                " (AID "+str(self.resolved.aid)+"). The existing roster and state lock are unchanged.",
                ephemeral=True)

    @discord.ui.button(label="Cancel",style=discord.ButtonStyle.secondary)
    async def cancel(self,interaction,button):
        for item in self.children:
            item.disabled=True
        await interaction.response.send_message("Alliance verification cancelled. Nothing changed.",
                                                 ephemeral=True)


async def _verify_managed_alliance(interaction,cog,name,state_id,tag,existing_id=None):
    from .membership_alliance_lookup import resolve
    await interaction.response.defer(ephemeral=True,thinking=True)
    resolved,error=await resolve(cog.bot,state_id,tag)
    if error:
        return await interaction.followup.send(error,ephemeral=True)
    embed=discord.Embed(
        title="Confirm Managed Alliance Identity",
        description=("**WOSBOT display name:** "+discord.utils.escape_markdown(name)+
                     "\\n**Game alliance:** ["+discord.utils.escape_markdown(resolved.tag)+
                     "] "+discord.utils.escape_markdown(resolved.name)+
                     "\\n**State:** "+str(resolved.state_id)+
                     "\\n**Verified AID:** "+str(resolved.aid)+
                     "\\n\\nThe exact tag capitalization is retained. "
                     "AID is the game identity; WOSBOT keeps its separate managed roster."),
        color=theme.emColor1)
    await interaction.followup.send(
        embed=embed,view=ConfirmManagedAllianceView(
            cog,interaction.user.id,interaction.guild_id,resolved,name,existing_id),
        ephemeral=True)


class VerifyExistingAllianceModal(discord.ui.Modal):
    """Bind an existing managed roster to its exact state and game tag."""
    def __init__(self,cog,alliance_id,name,state_id):
        super().__init__(title="Verify Existing Alliance")
        self.cog,self.alliance_id,self.name,self.state_id=cog,alliance_id,name,state_id
        self.tag=discord.ui.TextInput(label="Exact in-game alliance tag",required=True,max_length=20)
        self.add_item(self.tag)

    async def on_submit(self,interaction):
        if not _can_manage_target_alliance(interaction, self.alliance_id):
            return await interaction.response.send_message("Access denied.",ephemeral=True)
        info=alliance_identity.verified_identity(self.alliance_id)
        if (not info or info[0] is None
                or info[1] is None or int(info[1])!=self.state_id):
            return await interaction.response.send_message(
                "Home state or alliance settings changed. Open verification again.",ephemeral=True)
        await _verify_managed_alliance(interaction,self.cog,self.name,self.state_id,
                                       self.tag.value.strip(),self.alliance_id)


class AddAllianceModal(discord.ui.Modal):
    """Create managed alliance only after exact state/tag lookup and confirmation."""
    def __init__(self,cog):
        super().__init__(title="Add Alliance")
        self.cog=cog
        self.name_input=discord.ui.TextInput(
            label="WOSBOT display name",placeholder="Your managed alliance name",
            required=True,max_length=50)
        self.kid_input=discord.ui.TextInput(
            label="State #",placeholder="Home state number",required=True,max_length=10)
        self.tag_input=discord.ui.TextInput(
            label="Exact in-game alliance tag",placeholder="Case-sensitive tag, e.g. VEN",
            required=True,max_length=20)
        self.add_item(self.name_input)
        self.add_item(self.kid_input)
        self.add_item(self.tag_input)

    async def on_submit(self,interaction):
        if not _can_register_alliance(interaction):
            return await interaction.response.send_message(
                "Only Server Admin and above can create managed alliances.",ephemeral=True)
        name=self.name_input.value.strip()
        try:
            state=int(self.kid_input.value.strip())
            if state<=0 or not name:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "Provide a display name and positive home state number.",ephemeral=True)
        await _verify_managed_alliance(
            interaction,self.cog,name,state,self.tag_input.value.strip())


class EditNameModal(discord.ui.Modal):
    """Single-field alliance name editor. Updates alliance_list.name on submit."""

    def __init__(self, alliance_id: int, current_name: str):
        super().__init__(title="Edit Alliance Name")
        self.alliance_id = alliance_id
        self.name_input = discord.ui.TextInput(
            label="Alliance Name",
            placeholder="Enter the new alliance name",
            default=current_name,
            required=True,
            max_length=50,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.name_input.value.strip()
        if not new_name:
            await interaction.response.send_message(
                f"{theme.deniedIcon} Alliance name cannot be empty.", ephemeral=True
            )
            return
        try:
            await asyncio.to_thread(rename_alliance, self.alliance_id, new_name)

            embed = discord.Embed(
                title=f"{theme.verifiedIcon} Alliance Renamed",
                description=(
                    f"{theme.allianceIcon} **Name:** {new_name}\n"
                    f"{theme.fidIcon} **ID:** {self.alliance_id}"
                ),
                color=theme.emColor3,
            )
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception as e:
            logger.error(f"Error renaming alliance {self.alliance_id}: {e}")
            print(f"Error renaming alliance {self.alliance_id}: {e}")
            try:
                await interaction.response.send_message(
                    f"{theme.deniedIcon} Failed to rename alliance.", ephemeral=True
                )
            except Exception:
                pass


class EditStateModal(discord.ui.Modal):
    """Single-field editor for an alliance's home State (kid). A positive integer
    sets the home state used for redemption and member backfill; empty input clears
    it. This does NOT lock the alliance - locking is a separate toggle on the hub."""

    def __init__(self, alliance_id: int, alliance_name: str,
                 current_kid, conn, bot):
        super().__init__(title="Set Alliance State")
        self.alliance_id = alliance_id
        self.alliance_name = alliance_name
        self.conn = conn
        self.bot = bot
        self.kid_input = discord.ui.TextInput(
            label="State # (blank = clear)",
            placeholder="The alliance's state number",
            default=("" if current_kid is None else str(current_kid)),
            required=False,
            max_length=10,
        )
        self.add_item(self.kid_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = (self.kid_input.value or "").strip()
        new_kid = None
        if raw:
            try:
                new_kid = int(raw)
                if new_kid <= 0:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    f"{theme.deniedIcon} State must be a positive whole number "
                    f"(or leave blank to clear).",
                    ephemeral=True,
                )
                return
        try:
            cursor = self.conn.cursor()
            if new_kid is not None:
                # Set the home state; leave any existing lock in place (now applies to it).
                cursor.execute(
                    "UPDATE alliance_list SET kid = ?, multistate = 0, "
                    "game_aid = CASE WHEN kid = ? THEN game_aid ELSE NULL END, "
                    "game_tag = CASE WHEN kid = ? THEN game_tag ELSE NULL END "
                    "WHERE alliance_id = ?",
                    (new_kid,new_kid,new_kid,self.alliance_id),
                )
            else:
                # Clearing the home state can't leave a lock pointing at nothing.
                cursor.execute(
                    "UPDATE alliance_list SET kid = NULL, state_locked = 0, "
                    "game_aid = NULL, game_tag = NULL WHERE alliance_id = ?",
                    (self.alliance_id,),
                )
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error setting state on alliance {self.alliance_id}: {e}")
            print(f"Error setting state on alliance {self.alliance_id}: {e}")
            try:
                await interaction.response.send_message(
                    f"{theme.deniedIcon} Failed to update state.", ephemeral=True
                )
            except Exception:
                pass
            return

        if new_kid is None:
            result = (
                f"{theme.verifiedIcon} **{self.alliance_name}** home state cleared."
            )
        else:
            result = (
                f"{theme.verifiedIcon} **{self.alliance_name}** home state set to #{new_kid}. "
                f"Members can inherit it and redemption will use it. Use **State Lock** to also "
                f"reject players from other states."
            )

        # Return to the hub and report the result as a dismissible ephemeral.
        main_menu = self.bot.get_cog("MainMenu")
        if main_menu:
            await main_menu.show_alliance_hub(interaction, self.alliance_id)
            await interaction.followup.send(result, ephemeral=True)
        else:
            await interaction.response.send_message(result, ephemeral=True)


class PaginatedDeleteView(discord.ui.View):
    def __init__(self, pages, original_callback):
        super().__init__(timeout=menu_timeout())
        self.current_page = 0
        self.pages = pages
        self.original_callback = original_callback
        self.total_pages = len(pages)
        self.update_view()

    def update_view(self):
        self.clear_items()
        
        select = discord.ui.Select(
            placeholder=f"Select alliance to delete ({self.current_page + 1}/{self.total_pages})",
            options=self.pages[self.current_page]
        )
        select.callback = self.original_callback
        self.add_item(select)
        
        previous_button = discord.ui.Button(
            label="",
            emoji=f"{theme.prevIcon}",
            style=discord.ButtonStyle.grey,
            custom_id="previous",
            disabled=(self.current_page == 0)
        )
        previous_button.callback = self.previous_callback
        self.add_item(previous_button)

        next_button = discord.ui.Button(
            label="",
            emoji=f"{theme.nextIcon}",
            style=discord.ButtonStyle.grey,
            custom_id="next",
            disabled=(self.current_page == len(self.pages) - 1)
        )
        next_button.callback = self.next_callback
        self.add_item(next_button)

    async def previous_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page - 1) % len(self.pages)
        self.update_view()
        
        embed = discord.Embed(
            title=f"{theme.trashIcon} Delete Alliance",
            description=(
                f"**{theme.warnIcon} Warning: This action cannot be undone!**\n"
                f"{theme.upperDivider}\n"
                f"{theme.num1Icon} Select an alliance from the dropdown menu\n"
                f"{theme.num2Icon} Use {theme.prevIcon} {theme.nextIcon} buttons to navigate between pages\n\n"
                f"**Current Page:** {self.current_page + 1}/{self.total_pages}\n"
                f"**Total Alliances:** {sum(len(page) for page in self.pages)}\n"
                f"{theme.lowerDivider}"
            ),
            color=theme.emColor2
        )
        embed.set_footer(text=f"{theme.warnIcon} Warning: Deleting an alliance will remove all its data!")
        embed.timestamp = discord.utils.utcnow()

        await interaction.response.edit_message(embed=embed, view=self)

    async def next_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page + 1) % len(self.pages)
        self.update_view()

        embed = discord.Embed(
            title=f"{theme.trashIcon} Delete Alliance",
            description=(
                f"**{theme.warnIcon} Warning: This action cannot be undone!**\n"
                f"{theme.upperDivider}\n"
                f"{theme.num1Icon} Select an alliance from the dropdown menu\n"
                f"{theme.num2Icon} Use {theme.prevIcon} {theme.nextIcon} buttons to navigate between pages\n\n"
                f"**Current Page:** {self.current_page + 1}/{self.total_pages}\n"
                f"**Total Alliances:** {sum(len(page) for page in self.pages)}\n"
                f"{theme.lowerDivider}"
            ),
            color=theme.emColor2
        )
        embed.set_footer(text=f"{theme.warnIcon} Warning: Deleting an alliance will remove all its data!")
        embed.timestamp = discord.utils.utcnow()

        await interaction.response.edit_message(embed=embed, view=self)

class PaginatedChannelView(discord.ui.View):
    def __init__(self, channels, original_callback):
        super().__init__(timeout=menu_timeout())
        self.current_page = 0
        self.channels = channels
        self.original_callback = original_callback
        self.items_per_page = 25
        self.pages = [channels[i:i + self.items_per_page] for i in range(0, len(channels), self.items_per_page)]
        self.total_pages = len(self.pages)
        self.update_view()

    def update_view(self):
        self.clear_items()
        
        current_channels = self.pages[self.current_page]
        # Build options list without nested f-strings for Python 3.9+ compatibility
        channel_options = []
        for channel in current_channels:
            channel_label = f"#{channel.name}"[:100]
            # Determine description based on channel name length
            if len(f"#{channel.name}") > 40:
                option_description = f"Channel ID: {channel.id}"
            else:
                option_description = None

            channel_options.append(discord.SelectOption(
                label=channel_label,
                value=str(channel.id),
                description=option_description,
                emoji=theme.announceIcon
            ))
        
        select = discord.ui.Select(
            placeholder=f"Select channel ({self.current_page + 1}/{self.total_pages})",
            options=channel_options
        )
        select.callback = self.original_callback
        self.add_item(select)
        
        if self.total_pages > 1:
            previous_button = discord.ui.Button(
                label="",
                emoji=f"{theme.prevIcon}",
                style=discord.ButtonStyle.grey,
                custom_id="previous",
                disabled=(self.current_page == 0)
            )
            previous_button.callback = self.previous_callback
            self.add_item(previous_button)

            next_button = discord.ui.Button(
                label="",
                emoji=f"{theme.nextIcon}",
                style=discord.ButtonStyle.grey,
                custom_id="next",
                disabled=(self.current_page == len(self.pages) - 1)
            )
            next_button.callback = self.next_callback
            self.add_item(next_button)

    async def previous_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page - 1) % len(self.pages)
        self.update_view()
        
        embed = interaction.message.embeds[0]
        embed.description = (
            f"**Page:** {self.current_page + 1}/{self.total_pages}\n"
            f"**Total Channels:** {len(self.channels)}\n\n"
            "Please select a channel from the menu below."
        )
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def next_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page + 1) % len(self.pages)
        self.update_view()
        
        embed = interaction.message.embeds[0]
        embed.description = (
            f"**Page:** {self.current_page + 1}/{self.total_pages}\n"
            f"**Total Channels:** {len(self.channels)}\n\n"
            "Please select a channel from the menu below."
        )
        
        await interaction.response.edit_message(embed=embed, view=self)

async def setup(bot):
    conn = sqlite3.connect('db/alliance.sqlite')
    await bot.add_cog(Alliance(bot, conn))