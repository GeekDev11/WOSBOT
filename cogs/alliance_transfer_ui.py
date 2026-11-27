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
        self.stage, self.index, self.page = "alliance", 0, 0
        self.record, self.mapping, self.admin_role_id = None, {}, None
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
                    for channel in guild.text_channels[self.page * (25-len(choices)):(self.page+1) * (25-len(choices))])
            if choices:
                select = discord.ui.Select(
                    placeholder="Destination " + key.upper(), row=0, options=choices)
                select.callback = self.pick_channel
                self.add_item(select)
            self._button("Back", self.back)
            per_page = 24 if not self.record["channels"][key] else 25
            if self.page > 0:
                self._button("Previous channels", self.previous_channels)
            if guild and (self.page + 1) * per_page < len(guild.text_channels):
                self._button("More channels", self.more_channels)
        else:
            if self.stage == "review":
                guild = self.bot.get_guild(self.destination)
                options = [discord.SelectOption(label="No automatic admin role", value="0")]
                if guild:
                    options.extend(
                        discord.SelectOption(label=role.name[:90], value=str(role.id))
                        for role in guild.roles if not role.is_default() and not role.managed)
                picker = discord.ui.Select(
                    placeholder="Destination Alliance Admin role (optional)",
                    options=options[:25], row=0)
                picker.callback = self.choose_role
                self.add_item(picker)
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
                self.record["name"] + " — mapping " + str(self.index + 1) + "/" + str(len(service.KINDS)) + ": "
                + kind.upper() + "\nExisting: " + str(previous or "Not configured")
                + "\nSelect a destination channel. You may reuse the same channel for multiple purposes.")
        else:
            item = self.record
            pairs = "\n".join(
                k.upper() + ": " + (f"<#{self.mapping[k]}>" if self.mapping.get(k) else "Not configured")
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
                + "\nApproved destination Alliance Admin role: "
                + str(self.admin_role_id or "None; authorize admins later")
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

    async def choose_role(self, interaction):
        value = int(interaction.data["values"][0])
        self.admin_role_id = value or None
        await self.show(interaction)

    async def previous_channels(self, interaction):
        self.page = max(0, self.page - 1)
        await self.show(interaction)

    async def more_channels(self, interaction):
        self.page += 1
        await self.show(interaction)

    async def pick(self, interaction):
        self.record = await asyncio.to_thread(
            service.inspect, int(interaction.data["values"][0]))
        self.mapping = {kind: None for kind in service.KINDS}
        self.admin_role_id = None
        self.stage, self.index, self.page = "channels", 0, 0
        await self.show(interaction)

    async def pick_channel(self, interaction):
        key = service.KINDS[self.index]
        selected = int(interaction.data["values"][0])
        self.mapping[key] = selected or None
        self.index += 1
        self.page = 0
        if self.index == len(service.KINDS):
            self.stage = "review"
        await self.show(interaction)

    async def back(self, interaction):
        self.page = 0
        if self.stage == "confirm":
            self.stage = "review"
        elif self.stage == "review":
            self.stage, self.index = "channels", len(service.KINDS) - 1
        elif self.index:
            self.index -= 1
        else:
            self.stage = "alliance"
        await self.show(interaction)

    def check_idle(self):
        queue = self.bot.get_cog("ProcessQueue")
        if queue is None:
            raise ValueError("Process Queue unavailable; cannot verify safe transfer")
        running = queue.running_info()
        if running:
            raise ValueError("A bot operation is running. Finish it before transferring.")
        pending = queue.queued_processes()
        if pending:
            raise ValueError("Bot operations are queued. Finish them before transferring.")

    async def check(self):
        self.check_idle()
        current = await asyncio.to_thread(service.inspect, self.record["id"])
        if current != self.record:
            raise ValueError("Alliance settings changed. Restart the transfer wizard.")
        guild = self.bot.get_guild(self.destination)
        if guild is None:
            raise ValueError("Destination server is no longer connected")
        await asyncio.to_thread(
            service.validate, {**current, "mappings": self.mapping,
                               "admin_role_id": self.admin_role_id}, guild, self.bot)

    async def validate(self, interaction):
        try:
            await self.check()
        except (ValueError, OSError) as exc:
            await self.show(interaction, "Validation failed: " + str(exc))
            return
        self.stage = "confirm"
        await self.show(interaction, "Checks passed. Confirm to proceed.")

    async def execute(self, interaction):
        await interaction.response.defer()
        try:
            if not PermissionManager.is_owner(interaction.user.id):
                raise ValueError("Bot Owner permission changed")
            await self.check()
            backup = await asyncio.to_thread(
                service.transfer, self.record["id"],
                expected_source=self.record["source"], destination=self.destination,
                mappings=dict(self.mapping), actor_id=interaction.user.id,
                admin_role_id=self.admin_role_id)
        except Exception as exc:
            await interaction.followup.send(
                "Transfer was not completed: " + str(exc)[:1000]
                + ". Review the bot logs and backups before retrying.", ephemeral=True)
            return
        # Role grants and personal admin records changed in the database.
        PermissionManager._bust_role_cache()
        PermissionManager._admin_cache = None
        self.stop()
        await interaction.edit_original_response(
            embed=discord.Embed(
                title="Alliance transferred",
                description=self.record["name"] + " now belongs to this server. "
                            + "Member records and history were retained. Backup: " + backup,
                color=theme.emColor3), view=None)


async def show_transfer(bot, interaction):
    if (not interaction.guild_id
            or not PermissionManager.is_owner(interaction.user.id)):
        await interaction.response.send_message(
            "Only the Bot Owner can initiate an alliance transfer.", ephemeral=True)
        return
    view = TransferView(bot, interaction.user.id, interaction.guild_id)
    await view.show(interaction)
