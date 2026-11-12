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
