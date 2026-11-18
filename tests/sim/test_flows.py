"""Behaviour that is tedious to check by hand: permission tiers, missing Administrator,
expired menus, the menu timeout setting and stale confirmations."""
import pytest

from sim_harness import SETTINGS_TITLE, labels, title

pytestmark = pytest.mark.asyncio

STOPPED_WORKING = "This button stopped working"
# The five centers are private navigation for *all* administrator tiers.
# Permission restrictions apply to the selected tool, not to an ephemeral hub.
ADMIN_TIERS = ("owner", "global", "server", "alliance")
RESTRICTED_PERMISSION_TIERS = ("server", "alliance")


async def _expire(sim, seconds):
    await sim.env.advance_time(seconds + 1)


def _menu_seconds():
    from cogs.pimp_my_bot import menu_timeout
    return menu_timeout()


async def test_non_admin_is_refused(sim):
    result = await sim.users["member"].slash(sim.channel, "settings")
    # The shared entry defers the command before checking roles.
    reply = result.followups[-1] if result.followups else result.response
    assert result.acknowledged and reply is not None
    assert reply.ephemeral
    assert not reply.message.components
    assert "Administrator permission required" in reply.message.content


async def test_missing_administrator_is_explained(sim_without_admin):
    result = await sim_without_admin.users["owner"].slash(sim_without_admin.channel, "settings")
    reply = result.followups[-1] if result.followups else result.response
    assert result.acknowledged and reply is not None
    assert reply.ephemeral
    assert "Administrator" in reply.message.content


async def test_all_admin_tiers_can_open_private_centers(sim):
    """An ephemeral center is expected, not a permission-denied response."""
    for tier in ADMIN_TIERS:
        settings = await sim.open_settings(tier)
        for center in labels(settings, enabled_only=True):
            click = await sim.click(await sim.open_settings(tier), center, user=tier)
            assert not click.problems(), (tier, center, click.problems())
            assert click.screen is not None, (tier, center)
            message, ephemeral = click.screen
            assert ephemeral, (tier, center)
            assert "Choose a tool" in [
                getattr(item, "placeholder", None)
                for row in message.components for item in row.children
            ], (tier, center)


async def test_permission_management_restricted_to_global_admin(sim):
    """Verify the actual Permission Management tool, not its parent center."""
    for tier in ADMIN_TIERS:
        center = (
            await sim.click(
                await sim.open_settings(tier), "Administration & Settings", user=tier,
            )
        ).screen[0]
        choice = await sim.select(
            center, "Choose a tool", "Permission Management", user=tier,
        )
        assert not choice.problems(), (tier, choice.problems())
        assert choice.screen is not None, tier
        message, ephemeral = choice.screen
        assert ephemeral, tier
        if tier in RESTRICTED_PERMISSION_TIERS:
            assert "Only global administrators" in message.content, tier
        else:
            assert message.embeds, tier
            assert "Only global administrators" not in message.content, tier


async def test_click_on_expired_menu_is_answered(sim):
    """The opener should see the main menu reopen in place. That branch reads the deprecated
    message.interaction field, which SimCord does not send, so here every dead click gets the
    'stopped working' note; the test pins that a dead click is answered, not left failing."""
    hub = (await sim.click(await sim.open_settings(), "Gift Codes")).screen[0]
    await _expire(sim, _menu_seconds())
    click = await sim.click(sim.current(hub), labels(hub, enabled_only=True)[0])
    assert not click.problems()
    message, ephemeral = click.screen
    assert ephemeral and STOPPED_WORKING in message.content


async def test_menus_never_expire_when_timeout_is_zero(sim):
    from cogs.pimp_my_bot import set_menu_timeout_minutes
    set_menu_timeout_minutes(0)
    hub = (await sim.click(await sim.open_settings(), "Gift Codes")).screen[0]
    await _expire(sim, 30 * 24 * 3600)
    click = await sim.click(sim.current(hub), "Main Menu")
    assert not click.problems()
    assert title(click.screen[0]).endswith(SETTINGS_TITLE)


async def _open_remove_confirmation(sim, target):
    permissions = (await sim.click(await sim.open_settings(), "Permissions")).screen[0]
    admin = (await sim.select(permissions, "Open an admin…", target)).screen[0]
    return (await sim.click(admin, "Remove Admin")).screen[0]


async def test_expired_confirmation_is_replaced_by_notice(sim):
    from cogs.pimp_my_bot import confirm_timeout
    from cogs.permission_handler import PermissionManager, TIER_SERVER
    confirm = await _open_remove_confirmation(sim, "server-admin")
    assert "Confirm Remove" in title(confirm)
    await _expire(sim, confirm_timeout())
    expired = sim.current(confirm)
    assert "Confirm Remove" not in title(expired)
    assert "Confirm" not in labels(expired)
    assert PermissionManager.get_tier(sim.users["server"].id) == TIER_SERVER


@pytest.mark.parametrize("hub", ["Alliances", "Permissions", "Maintenance"])
async def test_menu_that_fails_to_open_says_so(sim, monkeypatch, hub):
    import cogs.bot_main_menu as main_menu

    async def broken_edit(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(main_menu, "safe_edit_message", broken_edit)
    click = await sim.click(await sim.open_settings(), hub)
    assert click.result.acknowledged
    message, ephemeral = click.screen
    assert ephemeral and "couldn't be opened" in message.content


async def test_restart_button_relaunches_in_place_in_a_container(sim, monkeypatch):
    import os
    import sys
    import cogs.bot_health as bot_health
    relaunched = []
    monkeypatch.setattr(os, "execl", lambda *args: relaunched.append(args))
    monkeypatch.setattr(bot_health, "is_container", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")  # hosting panels run Linux containers
    health = await sim.walk("Maintenance", "Bot Health")
    confirm = (await sim.click(health, "Restart Bot")).screen[0]
    click = await sim.click(confirm, "Confirm Restart")
    await sim.env.advance_time(5)
    assert relaunched, "the bot exited instead of relaunching in place"
    assert not click.raised
