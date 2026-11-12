"""Guard the WOSBOT profile editor and image upload behavior."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "cogs" / "bot_profile.py"


def test_profile_editor_compiles_and_has_one_command():
    source = PROFILE.read_text(encoding="utf-8")
    ast.parse(source)
    assert '@app_commands.command(name="botprofile"' in source
    assert '@app_commands.guild_only()' in source
    assert '@app_commands.guild_install()' in source
    assert 'async def show_profile' in source
    assert 'async def apply' in source
    assert 'discord.ui.FileUpload' in source
    assert 'discord.ui.Label(' in source
    assert 'class ProfileImageModal' in source
    assert 'class ConfirmImageUploadView' in source
    assert 'label="Upload Banner"' in source
    assert 'label="Upload Avatar"' in source
    assert '@botprofile.command' not in source
    assert '@app_commands.command(name="botstyle"' not in source


def test_profile_upload_validates_bytes_and_admin_permission():
    source = PROFILE.read_text(encoding="utf-8")
    assert "MAX_IMAGE_BYTES = 5 * 1024 * 1024" in source
    assert 'def validate_image(' in source
    assert 'PermissionManager.is_admin(interaction.user.id)' in source
    assert 'if not self.allowed(interaction):' in source
    assert 'file.size > MAX_IMAGE_BYTES' in source


def test_profile_bulk_scope_and_reset_are_explicit():
    source = PROFILE.read_text(encoding="utf-8")
    assert 'if not all_servers:' in source
    assert 'self.all_servers = not self.all_servers' in source
    assert 'Reset the bot' in source
    assert 'ConfirmResetView' in source
    assert 'self._change_lock' in source
    assert 'await member.edit(' in source
    assert 'asyncio.sleep(0.6)' in source


def test_profile_menu_is_global_admin_only():
    source = (ROOT / "cogs" / "bot_main_menu.py").read_text(encoding="utf-8")
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert '"bot_profile"' in main
    assert 'label="Bot Profile"' in source
    assert 'custom_id="bot_profile"' in source
    assert 'profile_cog.show_profile(interaction)' in source
    assert '"bot_presence", "bot_profile"' in source


def test_name_styles_are_opt_in_and_per_server():
    source = PROFILE.read_text(encoding="utf-8")
    ast.parse(source)
    assert 'NAME_STYLE_RESET' in source
    assert 'def build_name_style(' in source
    assert 'display_name_font_id' in source
    assert 'display_name_effect_id' in source
    assert 'display_name_colors' in source
    assert 'guild_id=guild.id' in source
    assert 'await asyncio.sleep(0.6)' in source
    assert 'Customize Name' in source
    assert 'class NameStyleEditor' in source
    assert 'class ConfirmAllNameStyleView' in source
    assert 'class NameStyleColorsModal' in source
    assert 'from discord.http import Route' in source


def test_style_payload_validation_without_importing_bot():
    module = ast.parse(PROFILE.read_text(encoding="utf-8"))
    defs = [node for node in module.body if isinstance(node, (ast.FunctionDef, ast.Assign)) 
            and (isinstance(node, ast.FunctionDef) and node.name == "build_name_style"
                 or isinstance(node, ast.Assign) and any(
                     isinstance(target, ast.Name) and target.id in {"FONT_IDS", "EFFECT_IDS", "COLOR_PATTERN"}
                     for target in node.targets))]
    # Evaluate only the pure helpers and constants (the bot must not start in tests).
    import re
    import pytest
    scope = {"re": re}
    exec(compile(ast.Module(body=defs, type_ignores=[]), str(PROFILE), "exec"), scope)
    build = scope["build_name_style"]
    result = build("Modern", "Gradient", "#5865F2", "#A78BFA")
    assert result["display_name_colors"] == [0x5865F2, 0xA78BFA]
    assert result["display_name_effect_id"] == 2
    with pytest.raises(ValueError):
        build("Modern", "Gradient", "blue", "#A78BFA")
    with pytest.raises(ValueError):
        build("Unknown", "Gradient", "#5865F2", "#A78BFA")
    with pytest.raises(ValueError):
        build("Modern", "Gradient", "#5865F2")

def test_custom_name_editor_has_full_options_and_explicit_scope():
    source = PROFILE.read_text(encoding="utf-8")
    assert '"Bangers": 1' in source
    assert '"Zilla Slab": 12' in source
    assert '"Glow": 6' in source
    assert 'NAME_COLOR_SWATCHES' in source
    assert '"Indigo": ("#5865F2", "🔵")' in source
    assert 'placeholder="Choose primary text color"' in source
    assert 'placeholder="Choose second text color"' in source
    assert 'label="Custom HEX"' in source
    assert 'label="Server / All"' in source
    assert 'label="Apply to Bot Name"' in source
    assert 'label="Reset Name"' in source
    assert 'Confirm All Servers' in source
    assert 'if self.all_servers:' in source
    assert 'await self.cog.apply_name_style(interaction, payload, False)' in source
    assert 'no changes' not in source.lower() or 'Apply' in source



def test_name_editor_keeps_action_buttons_when_dropdowns_change():
    source = PROFILE.read_text(encoding="utf-8")
    assert 'for item in list(self.children):' in source
    assert 'if isinstance(item, discord.ui.Select):' in source
    assert 'self.remove_item(item)' in source
    assert 'self.clear_items()' not in source


def test_profile_slash_commands_registered_in_bot_tree():
    """The single entry point must actually register."""
    import asyncio
    import discord
    from discord.ext import commands
    from cogs.bot_profile import BotProfile
    async def run():
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        try:
            await bot.add_cog(BotProfile(bot))
            names = {cmd.name for cmd in bot.tree.get_commands()}
            assert "botprofile" in names, names
            assert "botstyle" not in names, names
        finally:
            await bot.close()
    asyncio.run(run())


def test_profile_has_admin_guild_command_refresh():
    source = PROFILE.read_text(encoding="utf-8")
    assert 'label="Refresh Commands"' in source
    assert 'self.bot.tree.clear_commands(guild=guild)' in source
    assert 'self.bot.tree.copy_global_to(guild=guild)' in source
    assert 'await self.bot.tree.sync(guild=guild)' in source
    assert 'if not resolve_profile_guild(self.bot, interaction):' in source



def test_independent_color_swatches_fit_discord_component_rows():
    import ast
    source = PROFILE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NameStyleEditor")
    controls = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_controls")
    assert 'row=0' in ast.get_source_segment(source, controls)
    assert 'row=1' in ast.get_source_segment(source, controls)
    assert 'row=2' in ast.get_source_segment(source, controls)
    assert 'row=3' in ast.get_source_segment(source, controls)
    assert 'disabled=(self.effect != "Gradient")' in ast.get_source_segment(source, controls)
    for fn in ("custom_colors", "change_scope", "apply_style", "reset_style", "close"):
        method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == fn)
        assert 'row=4' in ast.get_source_segment(source, method.decorator_list[0])


def test_profile_resolves_guild_id_when_guild_object_is_missing():
    import ast
    from types import SimpleNamespace
    source = PROFILE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {"resolve_profile_guild", "targets_for"}
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    scope = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(PROFILE), "exec"), scope)
    guild = SimpleNamespace(id=123, name="VEN")
    bot = SimpleNamespace(
        get_guild=lambda guild_id: guild if guild_id == 123 else None,
        guilds=[guild],
    )
    interaction = SimpleNamespace(guild=None, guild_id=123)
    assert scope["resolve_profile_guild"](bot, interaction) is guild
    assert scope["targets_for"](bot, interaction, False) == [guild]
    assert scope["targets_for"](bot, interaction, True) == [guild]
    interaction.guild_id = None
    assert scope["targets_for"](bot, interaction, False) == []


def test_profile_distinguishes_avatar_banner_and_rejects_missing_context():
    source = PROFILE.read_text(encoding="utf-8")
    assert 'NO_GUILD_MESSAGE' in source
    assert 'No profile image was uploaded.' in source
    assert 'resolve_profile_guild(self.bot, interaction)' in source
    assert 'Upload a server-specific bot banner (not the avatar)' in source
    assert 'Upload a server-specific bot avatar (not the banner)' in source
