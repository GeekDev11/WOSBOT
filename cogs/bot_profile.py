"""Global-admin WOSBOT profile editor (official per-guild Discord member fields).

No experimental display-name API fields, no storing images or bot tokens.
"""
import asyncio
import io
import discord
from discord import app_commands
from discord.ext import commands
from .permission_handler import PermissionManager
from .pimp_my_bot import theme, safe_edit_message, menu_timeout

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def targets_for(bot, interaction, all_servers):
    if not all_servers:
        return [interaction.guild] if interaction.guild else []
    return sorted(bot.guilds, key=lambda guild: guild.id)


def validate_image(filename, content_type, data):
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Images must be 5 MB or smaller.")
    valid = (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or data.startswith((b"GIF87a", b"GIF89a"))
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )
    if not valid or content_type not in ALLOWED_TYPES:
        raise ValueError("Please upload a PNG, JPG, GIF, or WebP image.")
    return data


class ProfileTextModal(discord.ui.Modal, title="Edit WOSBOT Profile"):
    def __init__(self, cog, all_servers=False):
        super().__init__(timeout=300)
        self.cog = cog
        self.all_servers = all_servers
        self.nick = discord.ui.TextInput(
            label="Nickname", placeholder="WOSBOT", required=False, max_length=32
        )
        self.bio = discord.ui.TextInput(
            label="Bio", style=discord.TextStyle.paragraph, required=False,
            placeholder="Your Whiteout Survival companion", max_length=190
        )
        self.add_item(self.nick)
        self.add_item(self.bio)

    async def on_submit(self, interaction):
        if not self.cog.allowed(interaction):
            await interaction.response.send_message("Global Admin only.", ephemeral=True)
            return
        # Blank fields are left unchanged. Reset is a separate action.
        updates = {}
        if self.nick.value.strip():
            updates["nick"] = self.nick.value.strip()
        if self.bio.value.strip():
            updates["bio"] = self.bio.value.strip()
        if not updates:
            await interaction.response.send_message(
                "Enter at least one field. To clear fields, use Reset Profile.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(
            await self.cog.apply(interaction, updates, self.all_servers),
            ephemeral=True,
        )


class ProfileView(discord.ui.View):
    def __init__(self, cog, all_servers=False):
        super().__init__(timeout=menu_timeout())
        self.cog = cog
        self.all_servers = all_servers

    def embed(self):
        scope = "All servers" if self.all_servers else "This server"
        return discord.Embed(
            title="❄️ WOSBOT Profile",
            description=(
                f"**Editing:** {scope}\n\n"
                "Set a nickname or bio with **Edit Text**.\n"
                "To upload a banner or avatar, use the Discord slash commands:\n"
                "• `/botprofile banner`\n"
                "• `/botprofile avatar`\n\n"
                "Choose **All servers** in those commands to apply the same image "
                "to every server where the bot is present.\n\n"
                "**Reset** removes the per-server nickname, bio, avatar, and banner "
                "so Discord uses the default profile.\n\n"
                "Bot name fonts and effects aren't included because their bot API fields "
                "are undocumented."
            ),
            color=theme.emColor1,
        )

    async def check(self, interaction):
        if not self.cog.allowed(interaction):
            await interaction.response.send_message("Global Admin only.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="This Server / All Servers", emoji="🌍", style=discord.ButtonStyle.secondary)
    async def scope(self, interaction, button):
        if not await self.check(interaction):
            return
        self.all_servers = not self.all_servers
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Edit Text", emoji="✏️", style=discord.ButtonStyle.primary)
    async def edit_text(self, interaction, button):
        if await self.check(interaction):
            await interaction.response.send_modal(ProfileTextModal(self.cog, self.all_servers))

    @discord.ui.button(label="Reset Profile", emoji="🔄", style=discord.ButtonStyle.danger)
    async def reset(self, interaction, button):
        if not await self.check(interaction):
            return
        await interaction.response.send_message(
            "Reset the bot's per-server nickname, bio, avatar, and banner?"
            + (" This will affect **all servers**." if self.all_servers else " This server only."),
            view=ConfirmResetView(self.cog, self.all_servers, interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        if not await self.check(interaction):
            return
        main = self.cog.bot.get_cog("MainMenu")
        if main:
            await main.show_maintenance(interaction)


class ConfirmResetView(discord.ui.View):
    def __init__(self, cog, all_servers, user_id):
        super().__init__(timeout=60)
        self.cog = cog
        self.all_servers = all_servers
        self.user_id = user_id

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if interaction.user.id != self.user_id or not self.cog.allowed(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.apply(
            interaction, {"nick": None, "bio": None, "avatar": None, "banner": None},
            self.all_servers,
        )
        await interaction.followup.send(result, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Reset cancelled.", view=None)


class BotProfile(commands.Cog):
    botprofile = app_commands.Group(name="botprofile", description="Manage WOSBOT's server profile")

    def __init__(self, bot):
        self.bot = bot
        self._change_lock = asyncio.Lock()

    @staticmethod
    def allowed(interaction):
        return PermissionManager.is_admin(interaction.user.id)[1]

    async def show_profile(self, interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message("Global Admin only.", ephemeral=True)
            return
        view = ProfileView(self)
        await safe_edit_message(interaction, embed=view.embed(), view=view, content=None)

    async def apply(self, interaction, changes, all_servers=False):
        if not self.allowed(interaction):
            return "Global Admin only."
        if not interaction.guild:
            return "Run this command in a Discord server."
        if self._change_lock.locked():
            return "Another profile update is in progress."
        targets = targets_for(self.bot, interaction, all_servers)
        if not targets:
            return "No servers available."
        successful = []
        failures = []
        async with self._change_lock:
            for guild in targets:
                try:
                    member = guild.me or await guild.fetch_member(self.bot.user.id)
                    await member.edit(reason="WOSBOT profile updated by Global Admin", **changes)
                    successful.append(guild.name)
                except (discord.HTTPException, discord.Forbidden, ValueError, TypeError) as exc:
                    failures.append(f"{guild.name}: {str(exc)[:100]}")
                if all_servers:
                    await asyncio.sleep(0.6)
        msg = f"✅ Updated {len(successful)}/{len(targets)} server profiles."
        if failures:
            msg += "\n⚠️ " + "\n⚠️ ".join(failures[:8])
            if len(failures) > 8:
                msg += f"\n…and {len(failures) - 8} more failures."
        return msg[:1900]

    async def image_upload(self, interaction, file, all_servers, field):
        if not self.allowed(interaction):
            await interaction.response.send_message("Global Admin only.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("Use this command in a server.", ephemeral=True)
            return
        if file.size > MAX_IMAGE_BYTES:
            await interaction.response.send_message("File is larger than 5 MB.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            data = validate_image(file.filename, file.content_type, await file.read())
        except (ValueError, discord.HTTPException) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            await self.apply(interaction, {field: data}, all_servers),
            ephemeral=True,
        )

    @botprofile.command(name="avatar", description="Upload a server-specific bot avatar")
    @app_commands.describe(file="PNG, JPG, GIF, or WebP (up to 5 MB)", all_servers="Apply to all bot servers (Global Admin only)")
    async def avatar(self, interaction: discord.Interaction, file: discord.Attachment, all_servers: bool = False):
        await self.image_upload(interaction, file, all_servers, "avatar")

    @botprofile.command(name="banner", description="Upload a server-specific bot banner")
    @app_commands.describe(file="PNG, JPG, GIF, or WebP (up to 5 MB)", all_servers="Apply to all bot servers (Global Admin only)")
    async def banner(self, interaction: discord.Interaction, file: discord.Attachment, all_servers: bool = False):
        await self.image_upload(interaction, file, all_servers, "banner")


async def setup(bot):
    await bot.add_cog(BotProfile(bot))
