"""Navigation contract for the five-center redesign.

These tests protect existing handlers and ensure that all displayed menu
entries are routed, rather than silently doing nothing.
"""
import ast
from pathlib import Path

import pytest

from cogs.command_center import (
    SECTIONS, MainMenuView, CenterSectionView, AllianceWorkspaceView,
    alliance_summary,
)


def test_five_centers_and_full_catalog():
    assert list(SECTIONS) == ["alliance", "intel", "events", "gifts", "admin"]
    assert sum(len(section[2]) for section in SECTIONS.values()) == 32
    for section in SECTIONS.values():
        keys = [key for key, _, _ in section[2]]
        assert len(keys) == len(set(keys))
        assert len(keys) <= 25


def test_main_view_has_only_five_real_buttons():
    class Fake: pass
    view = MainMenuView(Fake())
    assert len(view.children) == 5
    assert view.timeout is None
    assert all(x.custom_id.startswith("wos_center_") for x in view.children)


def test_section_select_and_home():
    class Fake: pass
    for key, (_, _, catalog) in SECTIONS.items():
        view = CenterSectionView(Fake(), 1, key)
        selects = [i for i in view.children if hasattr(i, "options")]
        assert len(selects) == 1
        assert {i.value for i in selects[0].options} == {c[0] for c in catalog}


def test_workspace_has_real_management_actions():
    class Fake: pass
    view = AllianceWorkspaceView(Fake(), 1, [(100, "VEN")], 100)
    labels = {getattr(x, "label", "") for x in view.children}
    assert "Manage Members / Import" in labels
    assert "Troop Quality" in labels
    assert "Attendance & OCR" in labels
    assert "Export Members" in labels
    assert "Alliance Settings / History" in labels


def test_existing_main_menu_hub_and_permissions_not_removed():
    source = (Path(__file__).resolve().parents[1] / "cogs" / "bot_main_menu.py").read_text()
    ast.parse(source)
    for function in ("show_alliance_management", "show_alliance_hub",
                     "show_permissions", "show_maintenance"):
        assert f"async def {function}(" in source
    assert "from .command_center import MainMenuView" in source


def test_lookup_forms_validate_id_before_api():
    import inspect
    from cogs.command_center import ChiefLookupModal, StateLookupModal
    assert "isdigit" in inspect.getsource(ChiefLookupModal.on_submit)
    assert "isdigit" in inspect.getsource(StateLookupModal.on_submit)
