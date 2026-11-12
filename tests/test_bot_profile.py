"""Guard the WOSBOT profile editor and image upload behavior."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "cogs" / "bot_profile.py"


def test_profile_editor_compiles_and_has_required_commands():
    source = PROFILE.read_text(encoding="utf-8")
    ast.parse(source)
    assert 'app_commands.Group(name="botprofile"' in source
    assert '@botprofile.command(name="avatar"' in source
    assert '@botprofile.command(name="banner"' in source
    assert 'async def show_profile' in source
    assert 'async def apply' in source


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
    assert '@botprofile.command(name="style"' in source
    assert '@botprofile.command(name="style-reset"' in source
    assert 'guild_id=interaction.guild_id' in source
    assert 'No automatic retry' in source
    assert 'Aurora Name Style' in source
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
