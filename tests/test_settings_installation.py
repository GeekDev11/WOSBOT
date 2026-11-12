"""Regression checks for server-installation diagnostics in /settings."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "cogs" / "alliance.py"


def test_settings_distinguishes_no_bot_member_from_no_administrator_permission():
    text = SRC.read_text(encoding="utf-8")
    ast.parse(text)
    assert "if perm_check is None:" in text
    assert "if not perm_check.guild_permissions.administrator:" in text
    assert "A user-installed app can run slash commands" in text
    assert "Integrations → Bots and Apps" in text
    assert "Only grant Administrator if you" in text
