"""Member-state UI entry point.

Member States belongs to Alliance Management, not the Gift Code menu.
The existing state-scan and missed-code catch-up engine is still supplied by
GiftOperations; keep those processes intact while separating navigation.
"""

import discord

from . import alliance_member_states
from . import gift_redemption
from . import gift_state_resolver as state_resolver
from .pimp_my_bot import theme


async def show_member_states(bot, interaction: discord.Interaction):
    """Open Member States through its own service boundary.

    Resolving the existing engine here avoids exposing GiftOperations as the
    UI navigation destination and preserves its running scan/catch-up services.
    """
    engine = bot.get_cog("GiftOperations")
    if engine is None:
        await interaction.response.send_message(
            f"{theme.deniedIcon} Member States is unavailable. "
            "The state-scan service is not loaded. Check Bot Health → Modules.",
            ephemeral=True,
        )
        return

    await alliance_member_states.show_state_management(engine, interaction)


async def set_member_state_and_catch_up(engine, fid: int, kid: int, nickname: str):
    """Persist a single state, then queue the same missed-code catch-up as before."""
    import asyncio

    await asyncio.to_thread(state_resolver.set_user_kid, fid, kid)
    engine.logger.info(f"GiftOps: admin set state {kid} for FID {fid} ({nickname})")
    return gift_redemption.enqueue_member_redemption(engine, fid, nickname)


async def set_members_state_and_catch_up(engine, fids, kid: int):
    """Persist a bulk state update and enqueue catch-up for every listed member."""
    import asyncio

    # Take a stable snapshot so the update and catch-up use identical members.
    member_ids = list(fids)
    await asyncio.to_thread(state_resolver.set_user_kid_many, member_ids, kid)
    caught = sum(
        1 for fid in member_ids
        if gift_redemption.enqueue_member_redemption(engine, fid)
    )
    engine.logger.info(
        f"GiftOps: admin set state {kid} for {len(member_ids)} listed member(s)"
    )
    return caught
