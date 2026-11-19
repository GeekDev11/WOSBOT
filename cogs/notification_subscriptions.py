"""Default-on event pings through administrator-approved Discord roles.

The scheduler continues to post events publicly. Only members who have elected
to receive pings are mentioned when an event has a configured subscription role.
"""
import asyncio
import logging
from contextlib import contextmanager
import os
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from .notification_event_types import EVENT_CONFIG

log = logging.getLogger("notification")
DB_PATH = "db/beartime.sqlite"
EVENT_NAMES = tuple(EVENT_CONFIG) + ("Custom",)


@contextmanager
def _connection():
    os.makedirs("db", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""CREATE TABLE IF NOT EXISTS notification_subscription_roles (
        guild_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        role_id INTEGER NOT NULL,
        PRIMARY KEY (guild_id, event_type)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS notification_subscription_optouts (
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        PRIMARY KEY (guild_id, user_id, event_type)
    )""")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


async def event_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=name, value=name)
        for name in EVENT_NAMES if current.casefold() in name.casefold()
    ][:25]


class AlertPicker(discord.ui.View):
    def __init__(self, cog, member: discord.Member):
        super().__init__(timeout=180)
        self.cog = cog
        self.member_id = member.id
        self.guild_id = member.guild.id
        self._build(member)

    def _build(self, member):
        self.clear_items()
        entries = self.cog.available_roles(member.guild)
        if not entries:
            return
        options = [
            discord.SelectOption(
                label=event[:100],
                value=event,
                description=("Subscribed — select to leave" if role in member.roles
                             else "Not subscribed — select to join"),
                default=False,
                emoji="🔔" if role in member.roles else "🔕",
            )
            for event, role in entries[:25]
        ]
        select = discord.ui.Select(
            placeholder="Choose an event to join or leave",
            min_values=1, max_values=1, options=options,
        )

        async def changed(interaction: discord.Interaction):
            if not await self._check_user(interaction):
                return
            event = select.values[0]
            entry = dict(self.cog.available_roles(interaction.guild))
            role = entry.get(event)
            if role is None:
                await interaction.response.send_message(
                    "This subscription is no longer available.", ephemeral=True)
                return
            member = interaction.user
            if not isinstance(member, discord.Member):
                member = await interaction.guild.fetch_member(interaction.user.id)
            bot_member = interaction.guild.me
            if not bot_member or not bot_member.guild_permissions.manage_roles or role >= bot_member.top_role:
                await interaction.response.send_message(
                    "I cannot manage that role. Ask an admin to fix my role position and Manage Roles permission.",
                    ephemeral=True)
                return
            try:
                # Read the member's current role membership at click time.
                if role in member.roles:
                    await member.remove_roles(role, reason="Member opted out of event notifications")
                    self.cog.set_optout(self.guild_id, member.id, event, True)
                    action = "Unsubscribed from"
                else:
                    await member.add_roles(role, reason="Member opted in to event notifications")
                    self.cog.set_optout(self.guild_id, member.id, event, False)
                    action = "Subscribed to"
            except discord.Forbidden:
                await interaction.response.send_message(
                    "Discord denied this role change. Ask an admin to check role permissions.", ephemeral=True)
                return
            except discord.HTTPException:
                log.exception("Notification role toggle failed")
                await interaction.response.send_message(
                    "Could not update your subscription. Please try again.", ephemeral=True)
                return
            member = await interaction.guild.fetch_member(interaction.user.id)
            self._build(member)
            await interaction.response.edit_message(
                content=f"**{action} {event}.**\n\n{self.cog.summary(member)}",
                view=self,
            )

        select.callback = changed
        self.add_item(select)
        clear = discord.ui.Button(label="Unsubscribe from all", style=discord.ButtonStyle.secondary)

        async def clear_all(interaction: discord.Interaction):
            if not await self._check_user(interaction):
                return
            member = interaction.user
            if not isinstance(member, discord.Member):
                member = await interaction.guild.fetch_member(interaction.user.id)
            roles = [r for _, r in self.cog.available_roles(interaction.guild) if r in member.roles]
            bot_member = interaction.guild.me
            if roles and (not bot_member or not bot_member.guild_permissions.manage_roles
                          or any(r >= bot_member.top_role for r in roles)):
                await interaction.response.send_message(
                    "I cannot remove one of these roles. Contact an administrator.", ephemeral=True)
                return
            try:
                if roles:
                    await member.remove_roles(*roles, reason="Member opted out of all event notifications")
            except discord.HTTPException:
                await interaction.response.send_message(
                    "Could not remove your notification roles. Please try again.", ephemeral=True)
                return
            for event, _ in self.cog.available_roles(interaction.guild):
                self.cog.set_optout(self.guild_id, member.id, event, True)
            member = await interaction.guild.fetch_member(interaction.user.id)
            self._build(member)
            await interaction.response.edit_message(
                content=f"**Unsubscribed from all event notifications.**\n\n{self.cog.summary(member)}",
                view=self,
            )

        clear.callback = clear_all
        self.add_item(clear)

    async def _check_user(self, interaction):
        if interaction.guild_id != self.guild_id or interaction.user.id != self.member_id:
            await interaction.response.send_message(
                "Open your own notification preferences with /alerts.", ephemeral=True)
            return False
        return True


class NotificationSubscriptions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Cache mappings: the notification scheduler can run many times per second.
        with _connection() as conn:
            rows = conn.execute(
                "SELECT guild_id, event_type, role_id FROM notification_subscription_roles"
            ).fetchall()
        self._role_mapping = {(guild_id, event): role_id for guild_id, event, role_id in rows}

    async def cog_load(self):
        # Discord's privileged Server Members intent is required to backfill existing
        # members. Missing intent is reported rather than silently claiming success.
        self._sync_lock = asyncio.Lock()
        self._sync_task = asyncio.create_task(self._initial_sync())

    async def cog_unload(self):
        if getattr(self, "_sync_task", None):
            self._sync_task.cancel()

    def set_optout(self, guild_id: int, user_id: int, event: str, opted_out: bool):
        with _connection() as conn:
            if opted_out:
                conn.execute(
                    "INSERT OR IGNORE INTO notification_subscription_optouts "
                    "(guild_id, user_id, event_type) VALUES (?, ?, ?)",
                    (guild_id, user_id, event),
                )
            else:
                conn.execute(
                    "DELETE FROM notification_subscription_optouts "
                    "WHERE guild_id=? AND user_id=? AND event_type=?",
                    (guild_id, user_id, event),
                )

    def _optouts_for_guild(self, guild_id: int):
        with _connection() as conn:
            rows = conn.execute(
                "SELECT user_id, event_type FROM notification_subscription_optouts WHERE guild_id=?",
                (guild_id,),
            ).fetchall()
        return set(rows)

    async def _initial_sync(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            if self.available_roles(guild):
                await self._sync_guild(guild)

    async def _sync_guild(self, guild: discord.Guild):
        async with self._sync_lock:
            entries = self.available_roles(guild)
            if not entries:
                return
            me = guild.me
            if not me or not me.guild_permissions.manage_roles:
                log.warning("Cannot auto-subscribe guild %s: bot lacks Manage Roles", guild.id)
                return
            if not self.bot.intents.members:
                log.warning("Cannot auto-subscribe existing members in guild %s: Server Members intent disabled", guild.id)
                return
            assigned = 0
            failures = 0
            try:
                async for member in guild.fetch_members(limit=None):
                    if member.bot:
                        continue
                    # Read opt-outs at assignment time so a member toggling /alerts
                    # during backfill is not silently re-subscribed.
                    optouts = self._optouts_for_guild(guild.id)
                    roles = [
                        role for event, role in entries
                        if (member.id, event) not in optouts
                        and role not in member.roles
                        and role < me.top_role
                    ]
                    if not roles:
                        continue
                    try:
                        await member.add_roles(*roles, reason="Default event notification subscriptions")
                        assigned += 1
                    except (discord.Forbidden, discord.HTTPException):
                        failures += 1
                        log.warning("Could not auto-assign notification roles to member %s in guild %s", member.id, guild.id)
                    # Avoid bursts of role updates when backfilling existing members.
                    await asyncio.sleep(0.4)
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Cannot list members for default notification subscriptions in guild %s: %s", guild.id, exc)
            log.info("Default notification subscriptions in guild %s: %s updated, %s failed", guild.id, assigned, failures)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        me = member.guild.me
        if not me or not me.guild_permissions.manage_roles:
            return
        optouts = self._optouts_for_guild(member.guild.id)
        roles = [
            role for event, role in self.available_roles(member.guild)
            if (member.id, event) not in optouts
            and role not in member.roles and role < me.top_role
        ]
        if roles:
            try:
                await member.add_roles(*roles, reason="Default event notification subscriptions")
            except (discord.Forbidden, discord.HTTPException):
                log.warning("Could not add default notification roles for new member %s", member.id)

    def available_roles(self, guild: discord.Guild):
        rows = sorted(
            (event, role_id)
            for (guild_id, event), role_id in self._role_mapping.items()
            if guild_id == guild.id
        )
        return [
            (event, role)
            for event, role_id in rows
            if (role := guild.get_role(role_id)) is not None
            and not role.managed and role != guild.default_role
        ]

    def role_for_event(self, guild_id: int, event_type: str):
        return self._role_mapping.get((guild_id, event_type or "Custom"))

    def summary(self, member: discord.Member):
        entries = self.available_roles(member.guild)
        if not entries:
            return "No event notification roles have been configured by administrators yet."
        return "\n".join(
            f"{'🔔' if role in member.roles else '🔕'} **{event}** — "
            f"{'Subscribed' if role in member.roles else 'Not subscribed'}"
            for event, role in entries
        )

    @app_commands.command(name="alerts", description="Join or leave optional game event notifications")
    @app_commands.guild_only()
    async def alerts(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member):
            member = await interaction.guild.fetch_member(interaction.user.id)
        entries = self.available_roles(interaction.guild)
        view = AlertPicker(self, member) if entries else None
        await interaction.response.send_message(
            f"**Your event notifications**\n\n{self.summary(member)}\n\n"
            "Messages still appear in the event channel when you opt out; only role pings stop.",
            view=view, ephemeral=True,
        )

    @app_commands.command(name="alerts_setup", description="Admin: enable default-on event notification roles")
    @app_commands.guild_only()
    @app_commands.describe(event="Event type to subscribe to", role="Dedicated, non-privileged notification role")
    @app_commands.autocomplete(event=event_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def alerts_setup(self, interaction: discord.Interaction, event: str, role: discord.Role):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
            return
        if event not in EVENT_NAMES:
            await interaction.response.send_message("Select a valid event name from autocomplete.", ephemeral=True)
            return
        if role == interaction.guild.default_role or role.managed or role.permissions.value != 0:
            await interaction.response.send_message(
                "Use a dedicated role with **no server-wide permissions**. Managed roles and @everyone are not allowed.",
                ephemeral=True)
            return
        bot_member = interaction.guild.me
        if not bot_member or not bot_member.guild_permissions.manage_roles or role >= bot_member.top_role:
            await interaction.response.send_message(
                "Give the bot Manage Roles and move its highest role above this notification role.",
                ephemeral=True)
            return
        with _connection() as conn:
            conn.execute(
                "INSERT INTO notification_subscription_roles (guild_id, event_type, role_id) VALUES (?, ?, ?) "
                "ON CONFLICT(guild_id, event_type) DO UPDATE SET role_id=excluded.role_id",
                (interaction.guild_id, event, role.id),
            )
        self._role_mapping[(interaction.guild_id, event)] = role.id
        # Run the initial default-on role backfill without blocking the interaction.
        asyncio.create_task(self._sync_guild(interaction.guild))
        await interaction.response.send_message(
            f"**{event}** notifications now mention {role.mention}.\n"
            "Members are subscribed by default and may opt out via **/alerts**. "
            "Make this role mentionable or give the bot permission to mention roles. "
            "Existing-member enrollment requires the Server Members intent. "
            "Check channel-level role permissions before making a role self-assignable.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="alerts_remove", description="Admin: disable opt-in mapping for an event")
    @app_commands.guild_only()
    @app_commands.describe(event="Event type to remove")
    @app_commands.autocomplete(event=event_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def alerts_remove(self, interaction: discord.Interaction, event: str):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
            return
        with _connection() as conn:
            cur = conn.execute(
                "DELETE FROM notification_subscription_roles WHERE guild_id = ? AND event_type = ?",
                (interaction.guild_id, event),
            )
        self._role_mapping.pop((interaction.guild_id, event), None)
        with _connection() as conn:
            conn.execute("DELETE FROM notification_subscription_optouts WHERE guild_id=? AND event_type=?",
                         (interaction.guild_id, event))
        await interaction.response.send_message(
            f"Removed subscription role mapping for **{event}** ({cur.rowcount} mapping removed). "
            "The original notification mention setting now applies again.",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(NotificationSubscriptions(bot))
