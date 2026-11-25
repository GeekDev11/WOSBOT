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

    def embed(self, note=""):
        if self.stage == "alliance":
            description = "Choose an alliance from another server. Member and history data remain intact."
        elif self.stage == "channels":
            kind = service.KINDS[self.index]
            previous = self.record["channels"][kind]
            description = (
                self.record["name"] + " — mapping " + str(self.index + 1) + "/5: "
                + kind.upper() + "\nExisting: " + str(previous or "Not configured")
                + "\nSelect a corresponding destination-server channel.")
        else:
            item = self.record
            pairs = "\n".join(
                k.upper() + ": " + str(self.mapping.get(k) or "Not configured")
                for k in service.KINDS)
            description = (
                item["name"] + " (Alliance " + str(item["id"]) + ")"
                + "\nSource: " + str(item["source"])
                + " / Destination: " + str(self.destination)
                + "\nMembers preserved: " + str(item["members"])
                + "\n\nChannel mapping:\n" + pairs
                + "\n\nPermission review: "
                + str(item["personal_admins"]) + " personal alliance grants and "
                + str(item["role_grants"]) + " role grants will be revoked."
                + "\nBot Owner and Global Admin remain unchanged; destination Server Admin"
                + " permissions apply. Old Alliance Admins require new authorization."
                + "\nExisting player records remain, but old Discord registration server"
                + " bindings are cleared; notification roles/schedules are not copied."
                + "\nA backup is saved before execution.")
        if note:
            description += "\n\n" + note
        return discord.Embed(title="Transfer Alliance", description=description, color=theme.emColor1)

    async def show(self, interaction, note=""):
        self.rebuild()
        await safe_edit_message(interaction, embed=self.embed(note), view=self, content=None)

    async def pick(self, interaction):
        self.record = await asyncio.to_thread(
            service.inspect, int(interaction.data["values"][0]))
        self.mapping = {kind: None for kind in service.KINDS}
        self.stage, self.index = "channels", 0
        await self.show(interaction)

    async def pick_channel(self, interaction):
        key = service.KINDS[self.index]
        selected = int(interaction.data["values"][0])
        self.mapping[key] = selected or None
        self.index += 1
        if self.index == len(service.KINDS):
            self.stage = "review"
        await self.show(interaction)

    async def back(self, interaction):
        if self.stage == "confirm":
            self.stage = "review"
        elif self.stage == "review":
            self.stage, self.index = "channels", len(service.KINDS) - 1
        elif self.index:
            self.index -= 1
        else:
            self.stage = "alliance"
        await self.show(interaction)
