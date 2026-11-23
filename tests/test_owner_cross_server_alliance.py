"""Bot Owner may manage a home alliance from a different invoking guild."""
from types import SimpleNamespace
from unittest.mock import patch

from cogs import alliance


def interaction(guild, user=11):
    return SimpleNamespace(guild_id=guild, user=SimpleNamespace(id=user))


def test_bot_owner_cross_server_alliance_access():
    home = (100, 1501, 1501001154, "KiT", "KiT")
    with (patch.object(alliance.PermissionManager, "is_owner", return_value=True),
          patch.object(alliance.alliance_identity, "verified_identity", return_value=home)):
        assert alliance._can_manage_target_alliance(interaction(200), 9)


def test_server_admin_cannot_use_foreign_alliance():
    with (patch.object(alliance.PermissionManager, "is_owner", return_value=False),
          patch.object(alliance, "_alliance_in_guild", return_value=False),
          patch.object(alliance.PermissionManager, "can_access_alliance", return_value=True)):
        assert not alliance._can_manage_target_alliance(interaction(200), 9)


def test_unregistered_owner_target_is_denied():
    with (patch.object(alliance.PermissionManager, "is_owner", return_value=True),
          patch.object(alliance.alliance_identity, "verified_identity", return_value=None)):
        assert not alliance._can_manage_target_alliance(interaction(200), 9)


def test_local_admin_needs_alliance_permission():
    with (patch.object(alliance.PermissionManager, "is_owner", return_value=False),
          patch.object(alliance, "_alliance_in_guild", return_value=True),
          patch.object(alliance.PermissionManager, "can_access_alliance", return_value=False)):
        assert not alliance._can_manage_target_alliance(interaction(100), 9)
