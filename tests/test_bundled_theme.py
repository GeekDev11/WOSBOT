"""Checks for WOSBOT bundled theme presets."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_aurora_bundle_matches_theme_manager_format():
    data = json.loads((ROOT / "themes" / "WOSBOT_Aurora_theme.json").read_text(encoding="utf-8"))
    source = (ROOT / "cogs" / "pimp_my_bot.py").read_text(encoding="utf-8")
    assert data["themeName"] == "WOSBOT Aurora"
    assert len(data["icons"]) >= 100
    assert all(isinstance(v, str) and v for v in data["icons"].values())
    assert set(data["colors"]) == {
        "emColorString1", "emColorString2", "emColorString3",
        "emColorString4", "headerColor1", "headerColor2",
    }
    assert all(
        isinstance(value, str) and len(value) == 7
        and value.startswith("#")
        and all(ch in "0123456789ABCDEFabcdef" for ch in value[1:])
        for value in data["colors"].values()
    )
    for i in range(1, 4):
        divider = data["dividers"][f"divider{i}"]
        assert all(key in divider for key in ("start", "pattern", "end", "length"))
        assert 1 <= divider["length"] <= 30
    assert '"WOSBOT_Aurora_theme.json"' in source


def test_builtin_preset_install_is_not_destructive():
    source = (ROOT / "cogs" / "pimp_my_bot.py").read_text(encoding="utf-8")
    assert 'SELECT 1 FROM pimpsettings WHERE themeName=?' in source
    assert 'INSERT OR IGNORE INTO pimpsettings' in source
    assert '"is_active": 0' in source
    assert '"created_guild_id": None' in source
    assert 'if key in ICON_NAMES and key in columns' in source
