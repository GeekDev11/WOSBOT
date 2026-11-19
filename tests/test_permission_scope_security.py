"""Permission scope regressions: role caches, guild boundaries and alliance grants."""
import sqlite3

import pytest

from cogs.permission_handler import PermissionManager


@pytest.fixture
def permission_dbs(tmp_path, monkeypatch):
    settings = tmp_path / "settings.sqlite"
    alliances = tmp_path / "alliance.sqlite"
    with sqlite3.connect(settings) as db:
        db.executescript("""
            CREATE TABLE admin (id INTEGER PRIMARY KEY, is_initial INTEGER NOT NULL, is_owner INTEGER DEFAULT 0);
            CREATE TABLE adminserver (admin INTEGER, alliances_id INTEGER);
            CREATE TABLE admin_role (role_id INTEGER PRIMARY KEY, guild_id INTEGER, is_initial INTEGER);
            CREATE TABLE adminserver_role (role_id INTEGER, alliances_id INTEGER);
            INSERT INTO admin VALUES (1, 1, 1), (2, 0, 0), (3, 0, 0);
            INSERT INTO adminserver VALUES (3, 101);
            INSERT INTO admin_role VALUES (900, 10, 1);
            """)
    with sqlite3.connect(alliances) as db:
        db.executescript("""
            CREATE TABLE alliance_list (alliance_id INTEGER PRIMARY KEY, name TEXT, discord_server_id INTEGER);
            INSERT INTO alliance_list VALUES (101, 'A', 10), (102, 'B', 10), (201, 'C', 20);
            """)
    monkeypatch.setattr(PermissionManager, "SETTINGS_DB", str(settings))
    monkeypatch.setattr(PermissionManager, "ALLIANCE_DB", str(alliances))
    monkeypatch.setattr(PermissionManager, "_admin_cache", None)
    monkeypatch.setattr(PermissionManager, "_role_map_cache", None)
    monkeypatch.setattr(PermissionManager, "_role_grant_by_user", {})
    return settings, alliances


def test_server_admin_stays_with_current_server(permission_dbs):
    assert PermissionManager.get_admin_alliance_ids(2, 10) == ([101, 102], False)
    assert PermissionManager.get_admin_alliance_ids(2, 20) == ([201], False)
    assert not PermissionManager.can_access_alliance(2, 10, 201)


def test_alliance_admin_cannot_use_assignment_on_other_server(permission_dbs):
    assert PermissionManager.can_access_alliance(3, 10, 101)
    assert not PermissionManager.can_access_alliance(3, 10, 102)
    assert not PermissionManager.can_access_alliance(3, 20, 101)
    assert PermissionManager.get_admin_alliance_ids(3, 20) == ([], False)


def test_global_role_cannot_cross_guild_or_serve_in_dm(permission_dbs):
    PermissionManager.cache_member_role_grant(4, [900], 10)
    assert PermissionManager.is_global_admin(4, 10)
    assert not PermissionManager.is_global_admin(4, 20)
    assert not PermissionManager.is_global_admin(4, None)
    assert PermissionManager.can_access_alliance(4, 10, 201)


def test_personal_owner_global_scope_is_preserved(permission_dbs):
    assert PermissionManager.is_global_admin(1, None)
    assert PermissionManager.can_access_alliance(1, 20, 101)
