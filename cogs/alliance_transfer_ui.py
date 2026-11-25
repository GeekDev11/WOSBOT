"""Owner-only alliance transfer wizard."""
import asyncio
import discord
from .permission_handler import PermissionManager
from .pimp_my_bot import safe_edit_message, menu_timeout, theme
from . import alliance_transfer_service as service


class TransferView(discord.ui.View):
    def __init__(self, bot, actor, destination):
        super().__init__(timeout=menu_timeout())
        self.bot, self.actor, self.destination = bot, actor, destination
        self.stage, self.index = "alliance", 0
        self.record, self.mapping = None, {}
        self.rebuild()

    async def interaction_check(self, interaction):
        allowed = (interaction.user.id == self.actor
                   and interaction.guild_id == self.destination
                   and PermissionManager.is_owner(interaction.user.id))
        if not allowed:
            await interaction.response.send_message(
                "Only the Bot Owner can transfer alliances from the destination server.",
                ephemeral=True)
        return allowed

    def _button(self, label, callback, *, danger=False):
        button = discord.ui.Button(label=label, row=1, style=(
            discord.ButtonStyle.danger if danger else discord.ButtonStyle.secondary))
        button.callback = callback
        self.add_item(button)

    def rebuild(self):
        self.clear_items()
        if self.stage == "alliance":
            eligible = []
            for aid, name in PermissionManager.list_alliances():
                try:
                    if service.inspect(aid)["source"] != self.destination:
                        eligible.append((aid, name))
                except (OSError, ValueError):
                    pass
            if eligible:
                select = discord.ui.Select(
                    placeholder="Bring an alliance into this server", row=0,
                    options=[discord.SelectOption(label=str(name)[:90], value=str(aid))
                             for aid, name in eligible[:25]])
                select.callback = self.pick
                self.add_item(select)
        elif self.stage == "channels":
            key = service.KINDS[self.index]
            guild = self.bot.get_guild(self.destination)
            choices = []
            if not self.record["channels"][key]:
                choices.append(discord.SelectOption(label="Leave unconfigured", value="0"))
            if guild:
                choices.extend(
                    discord.SelectOption(label=channel.name[:90], value=str(channel.id))
                    for channel in guild.text_channels[:25 - len(choices)])
            if choices:
                select = discord.ui.Select(
                    placeholder="Destination " + key.upper(), row=0, options=choices)
                select.callback = self.pick_channel
                self.add_item(select)
            self._button("Back", self.back)
        else:
            self._button("Back", self.back)
            self._button(
                "Confirm Transfer" if self.stage == "confirm" else "Validate Permissions",
                self.execute if self.stage == "confirm" else self.validate,
                danger=self.stage == "confirm")
