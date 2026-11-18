"""Member-state UI entry point.

Member States belongs to Alliance Management, not the Gift Code menu.
The existing state-scan and missed-code catch-up engine is still supplied by
GiftOperations; keep those processes intact while separating navigation.
"""

import discord

from . import alliance_member_states
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
