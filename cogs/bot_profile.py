"""WOSBOT profile editor with opt-in experimental name styling.

Name styles use undocumented Discord bot fields; never apply automatically.
"""
import asyncio
import discord
import re
from discord.http import Route
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

# Community-observed IDs; not officially supported for bot accounts by Discord.
FONT_IDS = {
    "Bangers": 1, "BioRhyme": 2, "Sakura": 3, "Jellybean": 4,
    "Compagnon": 5, "Modern": 6, "Medieval": 7, "8Bit": 8,
    "Ribes": 9, "Sinistre": 10, "GG Sans": 11, "Zilla Slab": 12,
}
EFFECT_IDS = {"Solid": 1, "Gradient": 2, "Neon": 3, "Toon": 4, "Pop": 5, "Glow": 6}
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


def build_name_style(font, effect, primary, secondary=None):
    """Validate style before making a network request."""
    if font not in FONT_IDS or effect not in EFFECT_IDS:
        raise ValueError("Unknown font or effect.")
    if not isinstance(primary, str) or not COLOR_PATTERN.fullmatch(primary):
        raise ValueError("Primary color must look like #5865F2.")
    if effect == "Gradient":
        if not isinstance(secondary, str) or not COLOR_PATTERN.fullmatch(secondary):
            raise ValueError("Gradient requires a second color like #A78BFA.")
        colors = [int(primary[1:], 16), int(secondary[1:], 16)]
    else:
        colors = [int(primary[1:], 16)]
    return {
        "display_name_font_id": FONT_IDS[font],
        "display_name_effect_id": EFFECT_IDS[effect],
        "display_name_colors": colors,
    }


NAME_STYLE_RESET = {
    "display_name_font_id": None,
    "display_name_effect_id": None,
    "display_name_colors": None,
}



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
                "Use `all_servers: True` in those commands to apply the same image "
                "to every server where the bot is present.\n\n"
                "**Reset** removes the per-server nickname, bio, avatar, and banner "
                "so Discord uses the default profile.\n\n"
                "**Name Style:** click **Customize Name** for a font and effect list, "
                "color palettes or custom HEX colors, then apply to this server or all servers. "
                "You can also try `/botstyle` if slash commands are available. "
                "Discord may reject undocumented name-style fields, and server role colors "
                "can override the selected text colors."
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

    @discord.ui.button(label="Customize Name", emoji="🎨", style=discord.ButtonStyle.primary)
    async def customize_name(self, interaction, button):
        if not await self.check(interaction):
            return
        view = NameStyleEditor(self.cog, interaction.user.id, self.all_servers)
        await interaction.response.send_message(
            embed=view.embed(), view=view, ephemeral=True
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



# Named colors are quick choices; Custom Colors lets admins supply any HEX values.
NAME_COLOR_PRESETS = {
    "Aurora": ("#5865F2", "#A78BFA"),
    "Ice": ("#38BDF8", "#A5F3FC"),
    "Violet": ("#7C3AED", "#F472B6"),
    "Sunset": ("#F97316", "#EC4899"),
    "Ocean": ("#0284C7", "#14B8A6"),
    "Emerald": ("#10B981", "#A3E635"),
    "Rose": ("#FB7185", "#F9A8D4"),
    "Gold": ("#F59E0B", "#FDE68A"),
    "Crimson": ("#DC2626", "#F97316"),
    "Midnight": ("#334155", "#818CF8"),
    "Mono": ("#FFFFFF", "#94A3B8"),
}


class NameStyleColorsModal(discord.ui.Modal, title="Custom Name Colors"):
    def __init__(self, editor):
        super().__init__(timeout=300)
        self.editor = editor
        self.primary = discord.ui.TextInput(
            label="Primary color (HEX)", placeholder="#5865F2",
            default=editor.primary, min_length=7, max_length=7
        )
        self.secondary = discord.ui.TextInput(
            label="Second color (HEX)", placeholder="#A78BFA",
            default=editor.secondary, min_length=7, max_length=7
        )
        self.add_item(self.primary)
        self.add_item(self.secondary)

    async def on_submit(self, interaction):
        if not await self.editor.check(interaction):
            return
        a, b = self.primary.value.strip().upper(), self.secondary.value.strip().upper()
        if not COLOR_PATTERN.fullmatch(a) or not COLOR_PATTERN.fullmatch(b):
            await interaction.response.send_message(
                "Colors must have six HEX digits, for example `#5865F2`.",
                ephemeral=True,
            )
            return
        self.editor.primary, self.editor.secondary = a, b
        await interaction.response.edit_message(
            embed=self.editor.embed(), view=self.editor
        )


class NameStyleEditor(discord.ui.View):
    """Per-user, opt-in configuration. Nothing is sent to Discord until Apply."""
    def __init__(self, cog, user_id, all_servers=False):
        super().__init__(timeout=menu_timeout())
        self.cog = cog
        self.user_id = user_id
        self.all_servers = all_servers
        self.font = "Modern"
        self.effect = "Gradient"
        self.primary = "#5865F2"
        self.secondary = "#A78BFA"
        self._controls()

    def embed(self):
        scope = "All servers" if self.all_servers else "This server"
        preview = f"Primary: `{self.primary}`"
        if self.effect == "Gradient":
            preview += f" · Secondary: `{self.secondary}`"
        return discord.Embed(
            title="🎨 Customize Bot Name",
            description=(
                f"**Where:** {scope}\n"
                f"**Font:** {self.font}\n"
                f"**Effect:** {self.effect}\n"
                f"**Colors:** {preview}\n\n"
                "Choose a font, effect, and colors below, then click **Apply Style**. "
                "The embed border is only a color preview; it does not change the bot name. "
                "Use **Custom HEX** for any color you like. "
                "Apply does not rename the bot or change its avatar or banner.\n\n"
                "⚠️ Name-style fields are undocumented for bots. Discord can reject "
                "or ignore them. Role colors can override text colors in servers, "
                "and animations may not display on mobile."
            ),
            color=int(self.primary[1:], 16),
        )

    async def check(self, interaction):
        if interaction.user.id != self.user_id or not self.cog.allowed(interaction):
            await interaction.response.send_message(
                "Only the Global Admin who opened this editor can use it.",
                ephemeral=True,
            )
            return False
        return True

    def _controls(self):
        # Preserve the decorator-defined action buttons. clear_items() removed
        # Apply, Reset, Scope and Custom HEX every time a dropdown changed.
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)
        fonts = discord.ui.Select(
            placeholder="Font", row=0,
            options=[discord.SelectOption(
                label=name, value=name, default=(name == self.font)
            ) for name in FONT_IDS],
        )
        fonts.callback = self._change_font
        self.add_item(fonts)
        effects = discord.ui.Select(
            placeholder="Visual effect", row=1,
            options=[discord.SelectOption(
                label=name, value=name, default=(name == self.effect)
            ) for name in EFFECT_IDS],
        )
        effects.callback = self._change_effect
        self.add_item(effects)
        palette = discord.ui.Select(
            placeholder="Color palette", row=2,
            options=[discord.SelectOption(
                label=name, value=name,
                description=f"{colors[0]} / {colors[1]}"
            ) for name, colors in NAME_COLOR_PRESETS.items()],
        )
        palette.callback = self._change_palette
        self.add_item(palette)

    async def _change_font(self, interaction):
        if not await self.check(interaction):
            return
        self.font = interaction.data["values"][0]
        self._controls()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _change_effect(self, interaction):
        if not await self.check(interaction):
            return
        self.effect = interaction.data["values"][0]
        self._controls()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _change_palette(self, interaction):
        if not await self.check(interaction):
            return
        self.primary, self.secondary = NAME_COLOR_PRESETS[interaction.data["values"][0]]
        self._controls()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Custom HEX", emoji="🎨", row=3)
    async def custom_colors(self, interaction, button):
        if await self.check(interaction):
            await interaction.response.send_modal(NameStyleColorsModal(self))

    @discord.ui.button(label="Server / All", emoji="🌍", row=3)
    async def change_scope(self, interaction, button):
        if not await self.check(interaction):
            return
        self.all_servers = not self.all_servers
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Apply Style", emoji="✅",
                       style=discord.ButtonStyle.success, row=3)
    async def apply_style(self, interaction, button):
        if not await self.check(interaction):
            return
        try:
            payload = build_name_style(
                self.font, self.effect, self.primary, self.secondary
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if self.all_servers:
            await interaction.response.send_message(
                "Apply the selected name style to every server using this bot? "
                "This changes multiple server profiles.",
                view=ConfirmAllNameStyleView(self.cog, self.user_id, payload),
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        result = await self.cog.apply_name_style(interaction, payload, False)
        await interaction.followup.send(result, ephemeral=True)

    @discord.ui.button(label="Reset Style", emoji="↩️",
                       style=discord.ButtonStyle.secondary, row=4)
    async def reset_style(self, interaction, button):
        if not await self.check(interaction):
            return
        if self.all_servers:
            await interaction.response.send_message(
                "Remove the name style from every server using this bot?",
                view=ConfirmAllNameStyleView(
                    self.cog, self.user_id, NAME_STYLE_RESET.copy()
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        await interaction.followup.send(
            await self.cog.apply_name_style(
                interaction, NAME_STYLE_RESET.copy(), False
            ), ephemeral=True,
        )

    @discord.ui.button(label="Close", emoji="⬅️", row=4)
    async def close(self, interaction, button):
        if not await self.check(interaction):
            return
        await interaction.response.edit_message(content="Editor closed.", embed=None, view=None)


class ConfirmAllNameStyleView(discord.ui.View):
    def __init__(self, cog, user_id, payload):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id
        self.payload = payload.copy()

    @discord.ui.button(label="Confirm All Servers", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if interaction.user.id != self.user_id or not self.cog.allowed(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.apply_name_style(interaction, self.payload, True)
        await interaction.followup.send(result, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.edit_message(content="No changes made.", view=None)


class BotProfile(commands.Cog):
    botprofile = app_commands.Group(name="botprofile", description="Manage WOSBOT's server profile")

    def __init__(self, bot):
        self.bot = bot
        self._change_lock = asyncio.Lock()

    @staticmethod
    def allowed(interaction):
        return bool(PermissionManager.is_admin(interaction.user.id)[1])

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

    async def apply_name_style(self, interaction, payload, all_servers=False):
        """Explicitly apply experimental styling; report per-server success/uncertainty."""
        if not self.allowed(interaction) or not interaction.guild:
            return "Global Admin only. Run this command in a server."
        if self._change_lock.locked():
            return "Another profile change is in progress."
        targets = targets_for(self.bot, interaction, all_servers)
        results = []
        async with self._change_lock:
            for guild in targets:
                try:
                    route = Route("PATCH", "/guilds/{guild_id}/members/@me", guild_id=guild.id)
                    response = await self.bot.http.request(route, json=payload)
                    if isinstance(response, dict) and all(
                        response.get(field) == value for field, value in payload.items()
                    ):
                        results.append((guild.name, "confirmed"))
                    else:
                        results.append((guild.name, "unverified"))
                except discord.HTTPException as exc:
                    results.append((guild.name, f"rejected (HTTP {exc.status})"))
                except (TypeError, ValueError) as exc:
                    results.append((guild.name, f"failed: {str(exc)[:65]}"))
                if all_servers:
                    # No retry, and pace the per-server operations.
                    await asyncio.sleep(0.6)
        confirmed = sum(status == "confirmed" for _, status in results)
        unverified = sum(status == "unverified" for _, status in results)
        failures = len(results) - confirmed - unverified
        message = (f"Name style requests: {confirmed} confirmed, {unverified} unverified, "
                   f"{failures} rejected/failed across {len(results)} server(s).")
        for name, status in results[:10]:
            message += f"\n• {discord.utils.escape_markdown(name)[:50]}: {status}"
        if len(results) > 10:
            message += f"\n…and {len(results) - 10} more servers."
        if unverified:
            message += "\nDiscord did not confirm those fields; check the bot profile."
        return message[:1900]

    @app_commands.command(name="botstyle", description="Customize WOSBOT name font, effects and colors")
    @app_commands.guild_only()
    async def botstyle(self, interaction: discord.Interaction):
        """Direct menu access even if Discord's grouped slash commands are still propagating."""
        if not self.allowed(interaction):
            await interaction.response.send_message("Global Admin only.", ephemeral=True)
            return
        view = NameStyleEditor(self, interaction.user.id)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    @botprofile.command(name="style", description="Experimental font and colors for this server's bot name")
    @app_commands.describe(font="Display-name font", effect="Visual effect",
                           primary="First HEX color, e.g. #5865F2",
                           secondary="Second HEX color for Gradient, e.g. #A78BFA")
    @app_commands.choices(
        font=[app_commands.Choice(name=name, value=name) for name in FONT_IDS],
        effect=[app_commands.Choice(name=name, value=name) for name in EFFECT_IDS],
    )
    async def style(
        self, interaction: discord.Interaction,
        font: app_commands.Choice[str], effect: app_commands.Choice[str],
        primary: str, secondary: str = "",
    ):
        if not self.allowed(interaction):
            await interaction.response.send_message("Global Admin only.", ephemeral=True)
            return
        try:
            payload = build_name_style(font.value, effect.value, primary, secondary)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(
            await self.apply_name_style(interaction, payload), ephemeral=True
        )

    @botprofile.command(name="style-reset", description="Reset experimental name styling in this server")
    async def style_reset(self, interaction: discord.Interaction):
        if not self.allowed(interaction):
            await interaction.response.send_message("Global Admin only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(
            await self.apply_name_style(interaction, NAME_STYLE_RESET.copy()),
            ephemeral=True,
        )

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
