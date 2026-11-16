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
    assert "Manage Alliances" in labels


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


def test_state_comparison_is_real_modal_not_scout_redirect():
    import inspect
    from cogs.command_center import StateCompareModal, route
    assert "fetch_board" in inspect.getsource(StateCompareModal.on_submit)
    assert "StateCompareModal" in inspect.getsource(route)


def test_alliance_summary_includes_troop_coverage(tmp_path, monkeypatch):
    import sqlite3
    from cogs.command_center import alliance_summary
    monkeypatch.chdir(tmp_path)
    (tmp_path / "db").mkdir()
    with sqlite3.connect(tmp_path / "db" / "users.sqlite") as db:
        db.executescript("""
            CREATE TABLE users (
                fid INTEGER PRIMARY KEY, alliance TEXT, nickname TEXT,
                power INTEGER
            );
            CREATE TABLE member_troop_quality (
                fid INTEGER PRIMARY KEY,
                infantry_tier INTEGER, lancer_tier INTEGER, marksman_tier INTEGER
            );
            INSERT INTO users VALUES (1, '10', 'Alpha', 3000000);
            INSERT INTO users VALUES (2, '10', '', 2000000);
            INSERT INTO member_troop_quality VALUES (1, 11, 10, 12);
            INSERT INTO member_troop_quality VALUES (2, 11, NULL, 11);
        """)
    assert alliance_summary(10) == (2, 5000000, 1, 1)


def test_top_menu_requires_administrator():
    import inspect
    assert "is_admin" in inspect.getsource(MainMenuView.interaction_check)


def test_center_direct_entry_registered_for_servers_and_dms():
    from cogs.bot_main_menu import MainMenu
    cmd = MainMenu.center
    assert cmd.name == "center"
    assert cmd.allowed_installs.guild is True
    assert cmd.allowed_contexts.guild is True
    assert cmd.allowed_contexts.dm_channel is True


@pytest.mark.asyncio
async def test_section_dropdown_reads_select_not_home_button(monkeypatch):
    from types import SimpleNamespace
    import cogs.command_center as module
    routed = []
    async def fake_route(cog, interaction, section, choice):
        routed.append((section, choice))
    monkeypatch.setattr(module, "route", fake_route)
    view = CenterSectionView(object(), 11, "intel")
    # discord.py registers decorated buttons before dynamic selects.
    assert view.children[0] is not view.tool_selector
    view.tool_selector._values = ["scout"]
    await view.select_tool(SimpleNamespace())
    assert routed == [("intel", "scout")]


@pytest.mark.asyncio
async def test_alliance_switch_reads_select_not_action_button(monkeypatch):
    from types import SimpleNamespace
    import cogs.command_center as module
    opened = []
    async def fake_open(cog, interaction, alliance_id):
        opened.append(alliance_id)
    monkeypatch.setattr(module, "show_alliance_workspace", fake_open)
    view = AllianceWorkspaceView(object(), 11, [(100, "VEN"), (200, "ABC")], 100)
    assert view.children[0] is not view.alliance_selector
    view.alliance_selector._values = ["200"]
    await view.switch_alliance(SimpleNamespace())
    assert opened == [200]


@pytest.mark.asyncio
@pytest.mark.parametrize("section,choice", [
    (section, item[0]) for section, (_, _, items) in SECTIONS.items() for item in items
])
async def test_every_submenu_selection_routes_without_indexing_children(monkeypatch, section, choice):
    import cogs.command_center as module
    from types import SimpleNamespace
    forwarded = []
    async def fake_route(cog, interaction, name, tool):
        forwarded.append((name, tool))
    monkeypatch.setattr(module, "route", fake_route)
    view = CenterSectionView(object(), 123, section)
    view.tool_selector._values = [choice]
    interaction = SimpleNamespace()
    await view.select_tool(interaction)
    assert forwarded == [(section, choice)]


def test_selection_view_and_alliance_view_have_error_handlers():
    assert callable(CenterSectionView.on_error)
    assert callable(AllianceWorkspaceView.on_error)


def test_selection_descriptions_not_repeated_in_embed():
    import inspect
    from cogs.command_center import open_section
    source = inspect.getsource(open_section)
    assert "lines +=" not in source
    assert "dropdown" in source


@pytest.mark.asyncio
@pytest.mark.parametrize("section,choice", [
    (section, item[0]) for section, (_, _, items) in SECTIONS.items() for item in items
])
async def test_every_catalog_route_reaches_a_handler(monkeypatch, section, choice):
    import cogs.command_center as module
    from types import SimpleNamespace
    called = []

    async def fake_open_cog(*args, **kwargs):
        called.append(("cog", args[2], args[3]))
    async def fake_workspace(*args, **kwargs):
        called.append(("alliance",))
    async def fake_direct(*args, **kwargs):
        called.append(("direct",))
    async def fake_modal(modal):
        called.append(("modal", type(modal).__name__))

    monkeypatch.setattr(module, "open_cog", fake_open_cog)
    monkeypatch.setattr(module, "show_alliance_workspace", fake_workspace)
    monkeypatch.setattr(module, "show_alliance_settings_entry", fake_direct)
    cog = SimpleNamespace(
        show_permissions=fake_direct,
        show_maintenance=fake_direct,
        show_maintenance_tools=fake_direct,
        show_alliance_management=fake_direct,
        show_alliance_settings=fake_direct,
    )
    interaction = SimpleNamespace(response=SimpleNamespace(send_modal=fake_modal))
    await module.route(cog, interaction, section, choice)
    assert called, f"No handler routed for {section}/{choice}"


def test_back_navigation_uses_new_command_center():
    import inspect
    from cogs.bot_main_menu import MainMenu, AllianceHubView
    assert "show_alliance_workspace" in inspect.getsource(MainMenu.show_alliance_hub)
    assert "open_section" in inspect.getsource(MainMenu.show_alliance_management)
    assert "show_alliance_settings" in inspect.getsource(MainMenu)
    assert "show_alliance_hub" in inspect.getsource(AllianceHubView.back)
    assert "show_alliance_settings" in inspect.getsource(AllianceWorkspaceView.settings)


def test_settings_and_history_have_explicit_navigation():
    import inspect
    from cogs.command_center import route
    code = inspect.getsource(route)
    assert '("settings", "history")' in code
    assert "show_alliance_settings_entry" in code


def test_maintenance_back_returns_to_new_admin_center():
    import inspect
    from cogs.bot_main_menu import MainMenu
    assert "open_section" in inspect.getsource(MainMenu.show_maintenance)
    assert "MaintenanceView" in inspect.getsource(MainMenu.show_maintenance_tools)
    assert "show_maintenance_tools" in inspect.getsource(__import__("cogs.command_center", fromlist=["route"]).route)


def test_legacy_alliance_admin_tools_remain_available():
    import inspect
    from cogs.bot_main_menu import MainMenu
    assert "AllianceManagementEntryView" in inspect.getsource(MainMenu.show_alliance_admin_tools)
    assert "show_alliance_admin_tools" in inspect.getsource(AllianceWorkspaceView.manage_alliances)
