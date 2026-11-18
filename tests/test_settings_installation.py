"""Regression checks for the canonical /settings and /center entry path."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "cogs" / "bot_main_menu.py"
ALLIANCE = ROOT / "cogs" / "alliance.py"


def test_installation_diagnostics_live_in_shared_entry():
    text = MAIN.read_text(encoding="utf-8")
    ast.parse(text)
    assert "member = interaction.guild.me" in text
    assert "if member is None:" in text
    assert "if not member.guild_permissions.administrator:" in text
    assert "A user-installed app can run slash commands" in text
    assert "Integrations → Bots and Apps" in text
    assert "Only grant Administrator if you" in text


def test_settings_and_center_use_same_private_entry():
    main = MAIN.read_text(encoding="utf-8")
    alliance = ALLIANCE.read_text(encoding="utf-8")
    ast.parse(main)
    ast.parse(alliance)
    assert "await self.open_entry(interaction)" in main
    assert "await main_menu.open_entry(interaction)" in alliance
    assert "view=MainMenuView(self), ephemeral=True" in main
    assert "await self.bot.is_owner(interaction.user)" in main
    # Owner registration is intentionally offloaded so it cannot block Discord.
    assert "PermissionManager.add_admin, interaction.user.id, tier=TIER_OWNER" in main
    assert "await asyncio.to_thread(" in main
    assert "INSERT INTO admin (id, is_initial)" not in alliance
