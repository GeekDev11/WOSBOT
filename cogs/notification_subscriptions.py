"""Opt-in event pings through administrator-approved, self-assignable Discord roles.

The scheduler continues to post events publicly. Only members who have elected
to receive pings are mentioned when an event has a configured subscription role.
"""
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
                    action = "Unsubscribed from"
                else:
                    await member.add_roles(role, reason="Member opted in to event notifications")
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
        with _connection() as conn:
            pass

    def available_roles(self, guild: discord.Guild):
        with _connection() as conn:
            rows = conn.execute(
                "SELECT event_type, role_id FROM notification_subscription_roles WHERE guild_id = ? ORDER BY event_type",
                (guild.id,),
            ).fetchall()
        return [
            (event, role)
            for event, role_id in rows
            if (role := guild.get_role(role_id)) is not None
            and not role.managed and role != guild.default_role
        ]

    def role_for_event(self, guild_id: int, event_type: str):
        with _connection() as conn:
            row = conn.execute(
                "SELECT role_id FROM notification_subscription_roles WHERE guild_id = ? AND event_type = ?",
                (guild_id, event_type or "Custom"),
            ).fetchone()
        return row[0] if row else None

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

    @app_commands.command(name="alerts_setup", description="Admin: enable event opt-in using a dedicated Discord role")
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
        await interaction.response.send_message(
            f"**{event}** notifications now mention {role.mention}.\n"
            "Members can subscribe or unsubscribe with **/alerts**. "
            "Make this role mentionable or give the bot permission to mention roles. "
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
        await interaction.response.send_message(
            f"Removed subscription role mapping for **{event}** ({cur.rowcount} mapping removed). "
            "The original notification mention setting now applies again.",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(NotificationSubscriptions(bot))
