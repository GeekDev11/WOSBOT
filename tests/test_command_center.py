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
    assert sum(len(section[2]) for section in SECTIONS.values()) == 29
    assert "imports" not in [key for key, _, _ in SECTIONS["alliance"][2]]
    assert "reports" not in [key for key, _, _ in SECTIONS["events"][2]]
    assert "channels" not in [key for key, _, _ in SECTIONS["gifts"][2]]
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
    view = AllianceWorkspaceView(Fake(), 1, [(100, "EXA")], 100)
    labels = {getattr(x, "label", "") for x in view.children}
    assert {"Alliance Configuration", "Manage Alliances",
            "Back to Alliances", "Home"} <= labels
    assert not ({"Members & Imports", "Troop Quality", "Export Members",
                 "Rankings & History", "Attendance & OCR"} & labels)


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
    assert "StateComparisonCategoryView" in inspect.getsource(StateCompareModal.on_submit)
    assert "StateCompareModal(cog)" in inspect.getsource(route)


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
    view = AllianceWorkspaceView(object(), 11, [(100, "EXA"), (200, "ABC")], 100)
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
    monkeypatch.setattr(module, "show_alliance_history_entry", fake_direct)
    monkeypatch.setattr(module, "open_alliance_scoped_tool", fake_direct)
    monkeypatch.setattr(module, "show_gift_code_management", fake_direct)
    monkeypatch.setattr(module, "show_import_export_entry", fake_direct)
    monkeypatch.setattr(module, "open_gift_redemption", fake_direct)
    monkeypatch.setattr(module, "show_event_reports_entry", fake_direct)
    monkeypatch.setattr(module, "show_appearance_entry", fake_direct)
    monkeypatch.setattr(module, "show_server_management", fake_direct)
    cog = SimpleNamespace(
        show_permissions=fake_direct,
        show_maintenance=fake_direct,
        show_maintenance_tools=fake_direct,
        show_alliance_management=fake_direct,
        show_alliance_admin_tools=fake_direct,
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
    assert "show_alliance_settings" in inspect.getsource(AllianceWorkspaceView.configuration)


def test_settings_and_history_have_explicit_navigation():
    import inspect
    from cogs.command_center import route
    code = inspect.getsource(route)
    assert 'if key == "settings":' in code
    assert 'if key == "history":' in code
    assert "show_alliance_settings_entry" in code
    assert '"AllianceInsights", "show_alliance_insights"' in code


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


@pytest.mark.asyncio
async def test_legacy_alliance_hub_calls_new_workspace(monkeypatch):
    from types import SimpleNamespace
    from cogs.bot_main_menu import MainMenu
    import cogs.command_center as nav
    called = []
    async def workspace(cog, interaction, aid):
        called.append(aid)
    monkeypatch.setattr(nav, "show_alliance_workspace", workspace)
    menu = MainMenu(SimpleNamespace())
    await menu.show_alliance_hub(SimpleNamespace(), 101)
    assert called == [101]


@pytest.mark.asyncio
async def test_legacy_alliance_list_calls_new_center(monkeypatch):
    from types import SimpleNamespace
    from cogs.bot_main_menu import MainMenu
    import cogs.command_center as nav
    called = []
    async def section(cog, interaction, key):
        called.append(key)
    monkeypatch.setattr(nav, "open_section", section)
    menu = MainMenu(SimpleNamespace())
    await menu.show_alliance_management(SimpleNamespace())
    assert called == ["alliance"]


@pytest.mark.asyncio
async def test_old_maintenance_back_calls_new_admin_center(monkeypatch):
    from types import SimpleNamespace
    from cogs.bot_main_menu import MainMenu
    import cogs.command_center as nav
    called = []
    async def section(cog, interaction, key):
        called.append(key)
    monkeypatch.setattr(nav, "open_section", section)
    menu = MainMenu(SimpleNamespace())
    await menu.show_maintenance(SimpleNamespace())
    assert called == ["admin"]


@pytest.mark.asyncio
async def test_old_alliance_settings_back_goes_to_workspace():
    from types import SimpleNamespace
    from cogs.bot_main_menu import AllianceHubView
    called = []
    async def hub(interaction, alliance_id):
        called.append(alliance_id)
    cog = SimpleNamespace(show_alliance_hub=hub)
    view = AllianceHubView(cog, 25, "EXA", "alliance", [(25, "EXA", 71)])
    await view.back.callback(SimpleNamespace())
    assert called == [25]


def test_svs_modal_passes_state_id_not_scout_state():
    import inspect
    from cogs.command_center import StateLookupModal
    source = inspect.getsource(StateLookupModal.on_submit)
    assert '"state_id": int(state)' in source
    assert '"state": int(state)' in source


def test_distinct_modified_routes_and_direct_backup():
    import inspect
    from cogs.command_center import route, CoordinatesLookupModal
    source = inspect.getsource(route)
    assert 'CoordinatesLookupModal(cog)' in source
    assert '"NotificationSchedule", "show_main_menu"' in source
    assert '"BackupOperations", "show_backup_menu"' in source
    assert '"AllianceMemberOperations", "show_export_members"' in source
    assert 'open_gift_redemption' in source


@pytest.mark.asyncio
async def test_coordinates_are_not_redirected_to_player_inspector(monkeypatch):
    from cogs.command_center import route, CoordinatesLookupModal
    from types import SimpleNamespace
    opened = []
    async def send_modal(modal):
        opened.append(modal)
    await route(SimpleNamespace(), SimpleNamespace(response=SimpleNamespace(send_modal=send_modal)),
                "intel", "coordinates")
    assert len(opened) == 1
    assert isinstance(opened[0], CoordinatesLookupModal)


def test_scoped_workflows_and_profile_exposure():
    import inspect
    from cogs.command_center import route, AppearanceView, ImportExportView
    source = inspect.getsource(route)
    assert 'show_event_reports_entry(cog, interaction)' in source
    assert 'show_appearance_entry(cog, interaction)' in source
    assert 'open_alliance_scoped_tool' in inspect.getsource(ImportExportView)
    assert 'BotProfile' in inspect.getsource(AppearanceView)


def test_lookup_forms_use_instructions_not_personal_examples():
    from cogs.command_center import (
        ChiefLookupModal, StateLookupModal, StateCompareModal,
        AllianceIntelModal, CoordinatesLookupModal,
    )
    assert ChiefLookupModal.chief_id.placeholder == "Enter Chief ID"
    assert CoordinatesLookupModal.chief_id.placeholder == "Enter Chief ID"
    assert StateLookupModal.state_id.placeholder == "Enter state number"
    modal = StateCompareModal(object())
    assert modal.first.placeholder == "Enter first state number"
    assert modal.second.placeholder == "Enter second state number"
    assert AllianceIntelModal.state_id.placeholder == "Enter state number"
    assert AllianceIntelModal.tag.placeholder == "Enter alliance tag"


def test_intelligence_results_expose_parent_and_home_navigation():
    from cogs.command_center import (
        ResultNavigationView, AllianceHistoryPickView, AllianceActionPickView,
        ImportExportView, AppearanceView,
    )
    def labels(view):
        return {getattr(item, "label", None) for item in view.children}
    class Dummy: pass
    parent = ResultNavigationView(Dummy(), 1)
    assert {"Back to Intelligence", "Main Menu"} <= labels(parent)
    history = AllianceHistoryPickView(Dummy(), 1, [(10, "Example")])
    assert {"Back to Alliances", "Main Menu"} <= labels(history)
    scoped = AllianceActionPickView(Dummy(), 1, [(10, "Example")],
                                  "AttendanceReport", "show_session_selection", "Event Reports")
    assert scoped.parent_section == "events"
    assert {"Back to Center", "Main Menu"} <= labels(scoped)
    assert "Main Menu" in labels(ImportExportView(Dummy(), 1))
    assert "Main Menu" in labels(AppearanceView(Dummy(), 1))


def test_comparison_response_retains_navigation():
    import inspect
    from cogs.command_center import StateCompareModal, CoordinatesLookupModal
    assert "StateComparisonCategoryView" in inspect.getsource(StateCompareModal.on_submit)
    assert "send_lookup_result" in inspect.getsource(CoordinatesLookupModal.on_submit)


def test_state_comparison_categories_are_readable_and_selected():
    from cogs.command_center import StateComparisonCategoryView
    from cogs.wos_leaderboards import SCOUT_BOARDS, scout_label
    view = StateComparisonCategoryView(object(), 123, 101, 102)
    assert len(view.category_selector.options) == len(SCOUT_BOARDS)
    assert {option.value for option in view.category_selector.options} == set(SCOUT_BOARDS)
    assert all(option.label == scout_label(option.value) for option in view.category_selector.options)
    labels = {getattr(item, "label", None) for item in view.children}
    assert {"Back to Intelligence", "Main Menu"} <= labels


@pytest.mark.asyncio
async def test_state_comparison_category_switch_uses_saved_states():
    from types import SimpleNamespace
    from cogs.command_center import StateComparisonCategoryView
    class Leaderboards:
        def __init__(self):
            self.calls = []
        async def fetch_board(self, state, board, limit):
            self.calls.append((state, board, limit))
            return [{"score": 10}], None
    client = Leaderboards()
    class Bot:
        def get_cog(self, name):
            return client if name == "WosLeaderboards" else None
    async def defer():
        return None
    updates = []
    async def edit_original_response(**kw):
        updates.append(kw)
    view = StateComparisonCategoryView(SimpleNamespace(bot=Bot()), 123, 101, 102)
    view.category_selector._values = ["pet_power"]
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=defer),
        edit_original_response=edit_original_response,
    )
    await view.select_category(interaction)
    assert client.calls == [(101, "pet_power", 20), (102, "pet_power", 20)]
    assert view.first == 101 and view.second == 102
    assert updates[-1]["view"] is view
    await view.select_category(interaction)
    assert len(client.calls) == 2


def test_state_comparison_embed_displays_real_line_breaks():
    from cogs.command_center import StateComparisonCategoryView
    view = StateComparisonCategoryView(object(), 123, 101, 102)
    embed = view.build_embed(["**State 101:** API budget exhausted.",
                              "**State 102:** API budget exhausted."])
    assert "\n" in embed.description
    assert r"\n" not in embed.description
    assert "State 101:" in embed.description
    assert "State 102:" in embed.description


def test_alliance_settings_is_configuration_only_and_preserves_handlers():
    from cogs.bot_main_menu import AllianceHubView
    import inspect
    class Stub: pass
    view = AllianceHubView(Stub(), 10, "Example", "alliance",
                           [(10, "Example", 4)])
    labels = {getattr(item, "label", "") for item in view.children}
    assert {"Channel Setup", "Edit Name", "Set State", "Delete Alliance",
            "Back to Alliance Overview", "Main Menu"} <= labels
    assert not ({"Manage Members", "Troop Quality", "Power Rankings", "History"} & labels)
    # Hidden duplicate buttons were removed at source, not constructed and stripped.
    assert not hasattr(AllianceHubView, "manage_members")
    assert not hasattr(AllianceHubView, "power_rankings")
    from cogs.command_center import route
    source = inspect.getsource(route)
    assert '"AllianceMemberOperations", "show_manage_members_for"' in source
    assert '"AllianceInsights", "show_alliance_insights"' in source


def test_alliance_catalog_is_direct_and_attendance_belongs_to_events():
    alliance = {key: label for key, label, _ in SECTIONS["alliance"][2]}
    assert "attendance" not in alliance
    assert alliance["members"] == "Members & Imports"
    assert alliance["history"] == "Rankings & History"
    assert "management" in alliance
    assert "attendance" in {key for key, _, _ in SECTIONS["events"][2]}


def test_manage_gift_codes_is_not_duplicate_gift_dashboard():
    from cogs.command_center import GiftCodeManagementView
    view = GiftCodeManagementView(object(), 123)
    labels = {getattr(item, "label", None) for item in view.children}
    assert {"Add Gift Code", "List Gift Codes", "Delete Gift Code",
            "Back to Gift Center", "Main Menu"} <= labels
    assert "Redeem Gift Code" not in labels


def test_maintenance_has_no_duplicate_admin_entries():
    from cogs.bot_main_menu import MaintenanceView
    import discord
    view = MaintenanceView(object(), is_global=True, is_owner=True)
    ids = {x.custom_id for x in view.children if isinstance(x, discord.ui.Button)}
    assert not {"backup_system", "bot_health", "bot_profile", "leave_server"} & ids
    assert "check_updates" in ids
    assert any(getattr(x, "label", "") == "Back to Administration" for x in view.children)


def test_event_hubs_offer_back_to_events_and_home():
    import inspect
    from cogs.attendance import AttendanceHubView
    from cogs.bear_track import BearMenuView
    from cogs.minister_menu import MinisterChannelView
    from cogs.notification_system import BearTrapView
    from cogs.notification_schedule import ScheduleBoardMainView
    for view in (AttendanceHubView, BearMenuView, MinisterChannelView,
                 BearTrapView, ScheduleBoardMainView):
        assert "Back to Events" in inspect.getsource(view)
        assert "Main Menu" in inspect.getsource(view)


@pytest.mark.asyncio
async def test_redemption_history_back_uses_new_gift_center(monkeypatch):
    from types import SimpleNamespace
    from cogs.gift_redemption_results import RedeemHistoryView
    import cogs.command_center as nav
    sections = []
    async def open_gifts(cog, interaction, section):
        sections.append(section)
    monkeypatch.setattr(nav, "open_section", open_gifts)
    parent = object()
    gift_cog = SimpleNamespace(bot=SimpleNamespace(get_cog=lambda name: parent))
    view = RedeemHistoryView(gift_cog, 17, [], [], None)
    labels = {getattr(child, "label", None) for child in view.children}
    assert {"Back to Gift Center", "Main Menu"} <= labels
    await view._back(SimpleNamespace())
    assert sections == ["gifts"]


@pytest.mark.asyncio
async def test_redemption_history_home_uses_main_menu():
    from types import SimpleNamespace
    from cogs.gift_redemption_results import RedeemHistoryView
    calls = []
    class Main:
        async def show_main_menu(self, interaction):
            calls.append(interaction)
    gift_cog = SimpleNamespace(bot=SimpleNamespace(get_cog=lambda name: Main()))
    view = RedeemHistoryView(gift_cog, 17, [], [], None)
    interaction = SimpleNamespace()
    await view._home(interaction)
    assert calls == [interaction]


def test_server_settings_are_a_real_hub_not_direct_leave_server(monkeypatch):
    from cogs.command_center import SECTIONS, ServerManagementView
    from cogs.permission_handler import PermissionManager
    labels = {key: label for key, label, _ in SECTIONS["admin"][2]}
    assert labels["servers"] == "Server Management"
    monkeypatch.setattr(PermissionManager, "is_owner", lambda user_id: True)
    view = ServerManagementView(object(), 17)
    button_labels = {getattr(item, "label", None) for item in view.children}
    assert {"Server Overview", "Alliance Channel Setup",
            "Administrator Permissions", "Leave a Server",
            "Back to Administration", "Main Menu"} <= button_labels
    monkeypatch.setattr(PermissionManager, "is_owner", lambda user_id: False)
    viewer = ServerManagementView(object(), 17)
    assert "Leave a Server" not in {getattr(item, "label", None) for item in viewer.children}


@pytest.mark.asyncio
async def test_leave_server_back_returns_to_server_management(monkeypatch):
    from cogs.bot_health import ServersView
    import cogs.command_center as center
    from types import SimpleNamespace
    received = []
    async def fake_hub(cog, interaction):
        received.append(interaction)
    monkeypatch.setattr(center, "show_server_management", fake_hub)
    parent = SimpleNamespace()
    cog = SimpleNamespace(bot=SimpleNamespace(get_cog=lambda name: parent))
    view = ServersView(cog, None)
    interaction = SimpleNamespace()
    await view._on_back(interaction)
    assert received == [interaction]


@pytest.mark.asyncio
async def test_command_center_uses_service_not_raw_slash_callback(monkeypatch):
    """A menu action calls the shared handler and not the Command.callback object."""
    from types import SimpleNamespace
    import cogs.command_center as navigation
    called = []

    class Scout:
        async def show_scout(self, interaction, state):
            called.append(state)

    monkeypatch.setattr(navigation.PermissionManager, "is_admin",
                        staticmethod(lambda user_id: (True, True)))
    interaction = SimpleNamespace(user=SimpleNamespace(id=123))
    center = SimpleNamespace(bot=SimpleNamespace(
        get_cog=lambda name: Scout() if name == "WosStateScout" else None
    ))
    await navigation.invoke_slash(center, interaction, "WosStateScout", "scout", state=1501)
    assert called == [1501]
    assert ".callback" not in __import__("inspect").getsource(navigation.invoke_slash)


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [True, False])
async def test_empty_install_requires_discord_application_owner(tmp_path, monkeypatch, owner):
    """Both aliases acknowledge before checking an empty install."""
    import sqlite3
    from types import SimpleNamespace
    import cogs.bot_main_menu as menus

    database = tmp_path / "settings.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE admin (id INTEGER PRIMARY KEY)")
    monkeypatch.setattr(menus.PermissionManager, "SETTINGS_DB", str(database))
    monkeypatch.setattr(menus.PermissionManager, "is_admin",
                        staticmethod(lambda user_id: (True, True)))
    added, sent, acknowledged = [], [], []

    def add_admin(user_id, *, tier):
        assert acknowledged, "Must acknowledge the Discord command before database changes"
        added.append((user_id, tier))

    async def is_owner(user):
        assert acknowledged, "Must acknowledge before application owner lookup"
        return owner

    async def defer(**kwargs):
        acknowledged.append(kwargs)

    async def send_message(*args, **kwargs):
        sent.append((args, kwargs))

    monkeypatch.setattr(menus.PermissionManager, "add_admin", add_admin)
    bot = SimpleNamespace(is_owner=is_owner)
    interaction = SimpleNamespace(
        guild=None, user=SimpleNamespace(id=123),
        response=SimpleNamespace(defer=defer),
        followup=SimpleNamespace(send=send_message),
    )
    await menus.MainMenu(bot).open_entry(interaction)
    assert acknowledged == [{"thinking": True, "ephemeral": True}]
    assert bool(added) is owner
    if owner:
        assert added == [(123, menus.TIER_OWNER)]
        assert sent[0][1]["ephemeral"] is True
        assert sent[0][1]["view"] is not None
    else:
        assert sent[0][1]["ephemeral"] is True
        assert "application owner" in sent[0][0][0].lower()


@pytest.mark.asyncio
async def test_player_sync_back_returns_to_administration(monkeypatch):
    """Shared Player Sync has Administration as its canonical Back target."""
    from types import SimpleNamespace
    import cogs.command_center as navigation
    from cogs.alliance_api_menu import PlayerSyncView

    destinations = []

    async def open_parent(main, interaction, section):
        destinations.append(section)

    monkeypatch.setattr(navigation, "open_section", open_parent)
    main = SimpleNamespace()
    cog = SimpleNamespace(bot=SimpleNamespace(get_cog=lambda name: main))
    view = PlayerSyncView(cog, 123)
    interaction = SimpleNamespace()
    await view._on_back(interaction)
    assert destinations == ["admin"]


@pytest.mark.asyncio
async def test_attendance_empty_alliance_keeps_navigation(monkeypatch):
    """With zero assigned alliances, the hub is shown instead of a dead end."""
    from types import SimpleNamespace
    from cogs.attendance import Attendance
    import cogs.attendance as attendance_module

    monkeypatch.setattr(attendance_module.PermissionManager, "is_admin",
                        staticmethod(lambda user_id: (True, True)))
    monkeypatch.setattr(attendance_module.PermissionManager, "get_admin_alliances",
                        staticmethod(lambda user_id, guild_id: ([], False)))
    opened = []

    class Stub:
        async def show_attendance_hub(self, interaction, alliance_id=None):
            opened.append(alliance_id)

    guild = SimpleNamespace(id=10)
    interaction = SimpleNamespace(guild=guild, user=SimpleNamespace(id=123))
    await Attendance.show_attendance_menu(Stub(), interaction)
    assert opened == [None]


@pytest.mark.asyncio
async def test_alliance_history_back_returns_to_insights(monkeypatch):
    """History type picker should return to its Rankings & History parent."""
    from types import SimpleNamespace
    import cogs.command_center as nav
    from cogs.alliance_history import HistoryTypeView

    opened = []

    async def fake_insights(main, interaction, alliance_id):
        opened.append(alliance_id)

    monkeypatch.setattr(nav, "show_alliance_insights", fake_insights)
    main = object()
    cog = SimpleNamespace(bot=SimpleNamespace(get_cog=lambda name: main))
    view = HistoryTypeView(cog, 1501)
    assert any(item.label == "Back to Rankings & History" for item in view.children)
    await view.back.callback(SimpleNamespace())
    assert opened == [1501]


@pytest.mark.asyncio
async def test_self_registration_back_returns_to_manage_alliances():
    """Self-Registration belongs under Manage Alliances, not the root Alliance center."""
    from types import SimpleNamespace
    from cogs.bot_main_menu import SelfRegistrationView
    destinations = []

    async def manage(interaction):
        destinations.append("manage")

    cog = SimpleNamespace(show_alliance_admin_tools=manage)
    view = SelfRegistrationView(cog)
    await view._back(SimpleNamespace())
    assert destinations == ["manage"]


@pytest.mark.asyncio
async def test_member_states_back_returns_to_manage_alliances():
    from types import SimpleNamespace
    from cogs.alliance_member_states import StateManagementView
    destinations = []

    async def manage(interaction):
        destinations.append("manage")

    main = SimpleNamespace(show_alliance_admin_tools=manage)
    cog = SimpleNamespace(bot=SimpleNamespace(get_cog=lambda name: main))
    view = StateManagementView(cog, 123)
    await view._on_back(SimpleNamespace())
    assert destinations == ["manage"]


@pytest.mark.asyncio
async def test_permissions_back_returns_to_administration(monkeypatch):
    from types import SimpleNamespace
    from cogs.bot_main_menu import AdminManagerView
    import cogs.command_center as navigation
    import cogs.bot_main_menu as mm

    destinations = []

    async def allowed(*args, **kwargs):
        return True

    async def open_parent(cog, interaction, section):
        destinations.append(section)

    monkeypatch.setattr(mm, "check_interaction_user", allowed)
    monkeypatch.setattr(navigation, "open_section", open_parent)
    # Constructor does not load DB; it builds the entry view.
    view = AdminManagerView(SimpleNamespace(), 123)
    await view._on_back(SimpleNamespace())
    assert destinations == ["admin"]


@pytest.mark.asyncio
async def test_channel_setup_back_returns_to_alliance_configuration():
    from types import SimpleNamespace
    from cogs.alliance_channels import ChannelSetupView
    opened = []

    async def show_settings(interaction, alliance_id):
        opened.append(alliance_id)

    parent = SimpleNamespace(show_alliance_settings=show_settings)
    cog = SimpleNamespace(bot=SimpleNamespace(get_cog=lambda name: parent))
    view = ChannelSetupView(42, "VEN", cog)
    await view._on_back(SimpleNamespace())
    assert opened == [42]


@pytest.mark.asyncio
async def test_bot_profile_back_returns_to_appearance(monkeypatch):
    from types import SimpleNamespace
    import cogs.command_center as navigation
    from cogs.bot_profile import ProfileView

    returned = []

    async def fake_appearance(menu, interaction):
        returned.append("appearance")

    monkeypatch.setattr(navigation, "show_appearance_entry", fake_appearance)
    menu = object()
    cog = SimpleNamespace(
        allowed=lambda interaction: True,
        bot=SimpleNamespace(get_cog=lambda name: menu),
    )
    view = ProfileView(cog)
    await view.back.callback(SimpleNamespace())
    assert returned == ["appearance"]


@pytest.mark.asyncio
async def test_theme_back_returns_to_appearance(monkeypatch):
    """Guard the route and button label without requiring theme DB fixtures."""
    import inspect
    from cogs.pimp_my_bot import ThemeMenuView
    source = inspect.getsource(ThemeMenuView.back_to_settings)
    assert "show_appearance_entry" in source
    build = inspect.getsource(ThemeMenuView._build_components)
    assert "Back to Appearance" in build


def test_no_invalid_backtick_escapes_in_cogs():
    """Backticks inside Python strings must not have invalid backslash escapes."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "cogs"
    offenders = [
        str(path.relative_to(root.parent))
        for path in root.glob("*.py")
        if chr(92) + "`" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"Invalid Python backtick escape(s) in: {offenders}"


def test_manage_alliances_has_no_hidden_duplicate_controls():
    """Export and Player Sync have canonical menus, not stripped button definitions."""
    import inspect
    from cogs.bot_main_menu import AllianceManagementEntryView
    from cogs.command_center import SECTIONS

    assert not hasattr(AllianceManagementEntryView, "export_members")
    assert not hasattr(AllianceManagementEntryView, "player_sync")
    source = inspect.getsource(AllianceManagementEntryView.__init__)
    assert "alliance_entry_export" not in source
    assert "alliance_entry_player_sync" not in source
    assert any(key == "sync" for key, *_ in SECTIONS["admin"][2])
    assert any(key == "members" for key, *_ in SECTIONS["alliance"][2])
