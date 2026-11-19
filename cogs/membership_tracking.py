"""Alliance membership tracking menus; all callbacks enforce current guild scope."""
import asyncio
import logging
import discord
from discord.ext import commands

from . import membership_service as service
from .permission_handler import (PermissionManager, TIER_OWNER, TIER_GLOBAL, TIER_SERVER)
from .pimp_my_bot import theme, safe_edit_message, menu_timeout

log = logging.getLogger("membership")


def scope(interaction):
    if interaction.guild_id is None:
        return [], False
    ids, global_scope = PermissionManager.get_admin_alliance_ids(
        interaction.user.id, interaction.guild_id)
    tier = PermissionManager.get_effective_tier(interaction.user.id, interaction.guild_id)
    return list(map(int, ids)), global_scope or tier in (TIER_OWNER,TIER_GLOBAL,TIER_SERVER)


def allowed(interaction, alliance_id=None, server=False):
    ids, high = scope(interaction)
    if server and not high:
        return False
    if alliance_id is not None:
        if high:
            if server:
                return True
            return PermissionManager.can_access_alliance(
                interaction.user.id, interaction.guild_id, alliance_id)
        return int(alliance_id) in ids
    return bool(ids) or high


def visible_alliances(interaction):
    """Keep server-scoped views inside the active guild even for global admins."""
    if interaction.guild_id is None:
        return []
    choices=PermissionManager.get_admin_alliances(
        interaction.user.id,interaction.guild_id)[0]
    result=[]
    for aid,name in choices:
        info=service.home_info(aid)
        if info and info[0] is not None and int(info[0])==int(interaction.guild_id):
            result.append((int(aid),name))
    return result


async def deny(interaction):
    await interaction.response.send_message("Membership tracking: access denied.", ephemeral=True)


def _preview(text):
    return str(text).replace("@", "@\u200b").replace("`", "'")[:90]


class TrackingView(discord.ui.View):
    def __init__(self, cog, owner):
        super().__init__(timeout=menu_timeout())
        self.cog, self.owner = cog, owner

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner or not allowed(interaction):
            await deny(interaction)
            return False
        return True

    @discord.ui.button(label="Pending Actions", emoji="📋", style=discord.ButtonStyle.primary, row=0)
    async def cases(self, interaction, button):
        await self.cog.show_cases(interaction)

    @discord.ui.button(label="Temporary Alliances", emoji="🛡️", style=discord.ButtonStyle.secondary, row=0)
    async def temporary(self, interaction, button):
        await self.cog.show_registry(interaction)

    @discord.ui.button(label="Active Visits", emoji="🕒", style=discord.ButtonStyle.secondary, row=1)
    async def visitors(self, interaction, button):
        await self.cog.show_visits(interaction)

    @discord.ui.button(label="State Transfers", emoji="🗺️", style=discord.ButtonStyle.secondary, row=1)
    async def transfers(self, interaction, button):
        await self.cog.show_cases(interaction, transfers_only=True)

    @discord.ui.button(label="Movement History", emoji="📜", style=discord.ButtonStyle.secondary, row=2)
    async def history(self, interaction, button):
        await self.cog.show_history(interaction)

    @discord.ui.button(label="Tracking Settings", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2)
    async def settings(self, interaction, button):
        await self.cog.show_settings(interaction)

    @discord.ui.button(label="Back to Alliances", emoji="↩️", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction, button):
        from .command_center import open_section
        await open_section(self.cog.bot.get_cog("MainMenu"), interaction, "alliance")

    @discord.ui.button(label="Main Menu", emoji="🏠", style=discord.ButtonStyle.secondary, row=3)
    async def home(self, interaction, button):
        await self.cog.bot.get_cog("MainMenu").show_main_menu(interaction)


class Subview(discord.ui.View):
    def __init__(self, cog, owner):
        super().__init__(timeout=menu_timeout())
        self.cog, self.owner = cog, owner

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner or not allowed(interaction):
            await deny(interaction)
            return False
        return True

    @discord.ui.button(label="Back to Tracking", emoji="↩️", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction, button):
        await self.cog.show_tracking(interaction)

    @discord.ui.button(label="Main Menu", emoji="🏠", style=discord.ButtonStyle.secondary, row=4)
    async def home(self, interaction, button):
        await self.cog.bot.get_cog("MainMenu").show_main_menu(interaction)


class RegistryModal(discord.ui.Modal, title="Add Temporary Alliance"):
    def __init__(self, cog, state_id, alliance_id=None):
        super().__init__()
        self.cog, self.home_state, self.scope_alliance_id = cog, state_id, alliance_id
        self.state = discord.ui.TextInput(label="State ID", default=str(state_id), max_length=7)
        self.aid = discord.ui.TextInput(label="In-game alliance ID (AID)", max_length=20)
        self.tag = discord.ui.TextInput(label="Alliance tag", max_length=20)
        self.add_item(self.state)
        self.add_item(self.aid)
        self.add_item(self.tag)

    async def on_submit(self, interaction):
        if not allowed(interaction, self.scope_alliance_id, server=self.scope_alliance_id is None):
            return await deny(interaction)
        try:
            state = int(self.state.value)
            aid = int(self.aid.value)
            if self.scope_alliance_id is not None and state != self.home_state:
                raise ValueError("Alliance-specific rules must match the home state")
            await asyncio.to_thread(service.add_temporary, interaction.guild_id,
                                    state, aid, self.tag.value, interaction.user.id,
                                    self.scope_alliance_id)
        except (ValueError, TypeError) as exc:
            return await interaction.response.send_message(
                "Could not add temporary alliance: " + str(exc), ephemeral=True)
        await self.cog.show_registry(interaction)


class RegistryView(Subview):
    def __init__(self, cog, owner, rules, states, own_alliances, elevated):
        super().__init__(cog, owner)
        self.states, self.own_alliances, self.elevated = states, own_alliances, elevated
        if rules:
            opts = [discord.SelectOption(
                label=(_preview(r["tag"])+" · "+str(r["state_id"]))[:100],
                value=str(r["id"]),
                description=("State-wide" if r["scope_alliance_id"] is None else
                             "Alliance "+str(r["scope_alliance_id"]))) for r in rules[:25]]
            picker=discord.ui.Select(placeholder="Choose a rule to disable",options=opts,row=2)
            async def picked(interaction):
                target=int(picker.values[0])
                ids,high=scope(interaction)
                success=await asyncio.to_thread(service.remove_temporary,
                                                target,interaction.guild_id,ids,high)
                if not success:
                    return await deny(interaction)
                await cog.show_registry(interaction)
            picker.callback=picked
            self.add_item(picker)

    @discord.ui.button(label="Add State-wide", style=discord.ButtonStyle.primary, row=0)
    async def add_state(self, interaction, button):
        if not allowed(interaction, server=True):
            return await deny(interaction)
        if not self.states:
            return await interaction.response.send_message(
                "Set an alliance home state before adding a temporary destination.",
                ephemeral=True)
        await interaction.response.send_modal(RegistryModal(self.cog,self.states[0]))

    @discord.ui.button(label="Add for My Alliance", style=discord.ButtonStyle.secondary, row=0)
    async def add_scoped(self, interaction, button):
        if not self.own_alliances:
            return await deny(interaction)
        aid=self.own_alliances[0]
        info=service.home_info(aid)
        if not info or info[1] is None:
            return await interaction.response.send_message("This alliance has no home state set.", ephemeral=True)
        await interaction.response.send_modal(RegistryModal(self.cog,int(info[1]),aid))



class ConfirmActionView(Subview):
    def __init__(self,cog,owner,case,action,destination=None):
        super().__init__(cog,owner)
        self.case,self.action,self.destination=case,action,destination

    @discord.ui.button(label="Confirm Membership Action",style=discord.ButtonStyle.danger,row=0)
    async def confirm(self,interaction,button):
        src=int(self.case["home_alliance"])
        if not allowed(interaction,src):
            return await deny(interaction)
        ids,_=scope(interaction)
        visible={int(aid) for aid,_ in visible_alliances(interaction)}
        if src not in visible:
            return await deny(interaction)
        destination_ids=set()
        if self.action=="move":
            dest=self.destination
            if dest not in visible:
                return await deny(interaction)
            info=service.home_info(dest)
            if not info or int(info[0])!=int(interaction.guild_id):
                return await deny(interaction)
            destination_ids.add(dest)
        try:
            success=await asyncio.to_thread(service.apply_roster_action,
                self.case["id"],self.case["version"],interaction.user.id,
                [src],self.action,self.destination,destination_ids)
        except Exception:
            log.exception("Membership action failed")
            return await interaction.response.send_message(
                "Action failed without confirmation. Check Bot Health logs.",ephemeral=True)
        if not success:
            return await interaction.response.send_message(
                "Member moved or this case changed. Open the queue again.",ephemeral=True)
        await self.cog.show_cases(interaction)

    @discord.ui.button(label="Cancel",style=discord.ButtonStyle.secondary,row=0)
    async def cancel(self,interaction,button):
        await self.cog.show_cases(interaction)


class MoveDestinationView(Subview):
    def __init__(self,cog,owner,case,destinations):
        super().__init__(cog,owner)
        opts=[discord.SelectOption(label=name[:75],value=str(aid),
                                   description="Managed alliance ID "+str(aid))
              for aid,name in destinations[:25]]
        picker=discord.ui.Select(placeholder="Choose new home alliance",options=opts,row=0)
        async def selected(interaction):
            src=int(case["home_alliance"])
            dest=int(picker.values[0])
            if not allowed(interaction,src) or not allowed(interaction,dest):
                return await deny(interaction)
            await confirm_roster_action(cog,interaction,case,"move",dest)
        picker.callback=selected
        self.add_item(picker)


async def confirm_roster_action(cog,interaction,case,action,dest=None):
    detail={
        "move":"The member's managed home alliance will change to "+str(dest)+".",
        "archive":"The member leaves the active roster. A recoverable snapshot is saved.",
        "remove":"The member is removed from the active roster. A recovery snapshot is retained."
    }[action]
    embed=discord.Embed(
        title="Confirm "+action.title()+" · Chief "+str(case["fid"]),
        description="**Home alliance:** "+str(case["home_alliance"])+"\n"+
                    detail+"\n\nThis action modifies the managed roster. "
                    "The decision and actor are recorded for audit.",
        color=theme.emColor1)
    await safe_edit_message(interaction,embed=embed,
        view=ConfirmActionView(cog,interaction.user.id,case,action,dest),content=None)


class CaseView(Subview):
    def __init__(self,cog,owner,case,transfers_only=False):
        super().__init__(cog,owner)
        self.case=case
        self.transfers_only=transfers_only
        picker=discord.ui.Select(placeholder="Resolve case",row=1,options=[
            discord.SelectOption(label="Keep active in home roster",value="keep"),
            discord.SelectOption(label="Mark temporary visit",value="temporary"),
            discord.SelectOption(label="Move to managed alliance",value="move"),
            discord.SelectOption(label="Archive from active roster",value="archive"),
            discord.SelectOption(label="Remove from active roster",value="remove")
        ])
        async def select(interaction):
            if not allowed(interaction,case["home_alliance"]):
                return await deny(interaction)
            action=picker.values[0]
            if action in ("archive","remove"):
                return await confirm_roster_action(cog,interaction,case,action)
            if action=="move":
                visible=visible_alliances(interaction)
                candidates=[(int(aid),name) for aid,name in visible
                            if int(aid)!=int(case["home_alliance"]) and
                            (service.home_info(aid) or [None])[0]==interaction.guild_id]
                if not candidates:
                    return await interaction.response.send_message(
                        "No other authorized managed alliance is available.",ephemeral=True)
                embed=discord.Embed(title="Move Member",
                                    description="Select the new managed home alliance. "
                                                "This is a permanent move, not a temporary visit.",
                                    color=theme.emColor1)
                return await safe_edit_message(interaction,embed=embed,
                    view=MoveDestinationView(cog,interaction.user.id,case,candidates),content=None)
            if action=="temporary" and case["category"]=="state_transfer":
                return await interaction.response.send_message(
                    "State transfers require a separate decision; temporary visits are same-state only.",
                    ephemeral=True)
            success=await asyncio.to_thread(service.decide,case["id"],case["version"],
                                            interaction.user.id,[case["home_alliance"]],action)
            if not success:
                return await interaction.response.send_message(
                    "Case was already resolved or changed. Reopen the queue.",ephemeral=True)
            await cog.show_cases(interaction,transfers_only=transfers_only)
        picker.callback=select
        self.add_item(picker)

    @discord.ui.button(label="Back to Cases",style=discord.ButtonStyle.secondary,row=2)
    async def cases(self,interaction,button):
        await self.cog.show_cases(interaction,self.transfers_only)


class CaseListView(Subview):
    def __init__(self,cog,owner,cases,transfers_only):
        super().__init__(cog,owner)
        if cases:
            picker=discord.ui.Select(placeholder="Select case",row=0,options=[
                discord.SelectOption(label=("#"+str(c["id"])+" · "+str(c["fid"]))[:100],
                                    value=str(c["id"]),
                                    description=(c["category"]+" · "+(c["game_tag"] or "unknown"))[:100])
                for c in cases[:25]])
            async def select(interaction):
                selected=next((c for c in cases if c["id"]==int(picker.values[0])),None)
                if not selected or not allowed(interaction,selected["home_alliance"]):
                    return await deny(interaction)
                embed=discord.Embed(title="Membership Case #"+str(selected["id"]),
                    description=("**Chief ID:** "+str(selected["fid"])+"\n"
                                 "**Home Alliance:** "+str(selected["home_alliance"])+"\n"
                                 "**State:** "+str(selected["from_state"])+" → "+str(selected["to_state"])+"\n"
                                 "**Observed:** "+_preview(selected["game_tag"] or "Unknown")+"\n"
                                 "**Type:** "+selected["category"]+"\n\n"
                                 "Keep retains active membership. Temporary preserves the home roster. "
                                 "Move, Archive and Remove require an additional confirmation "
                                 "and preserve an audited recovery record."),
                    color=theme.emColor1)
                await safe_edit_message(interaction,embed=embed,
                    view=CaseView(cog,interaction.user.id,selected,transfers_only))
            picker.callback=select
            self.add_item(picker)


class SettingsView(Subview):
    @discord.ui.button(label="Enable / Disable Tracking",style=discord.ButtonStyle.primary,row=0)
    async def toggle(self,interaction,button):
        if not allowed(interaction,server=True):
            return await deny(interaction)
        current=await asyncio.to_thread(service.is_enabled,interaction.guild_id)
        await asyncio.to_thread(service.set_enabled,interaction.guild_id,not current)
        await self.cog.show_settings(interaction)


class MembershipTracking(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
        service.initialize()

    async def show_tracking(self,interaction):
        if not allowed(interaction):
            return await deny(interaction)
        ids,_=scope(interaction)
        if not ids:
            ids=[int(aid) for aid,_ in visible_alliances(interaction)]
        stats=await asyncio.to_thread(service.dashboard,interaction.guild_id,ids)
        enabled=await asyncio.to_thread(service.is_enabled,interaction.guild_id)
        embed=discord.Embed(title="🛡️ Membership Tracking",
            description=("**Status:** "+("Monitoring enabled" if enabled else "Disabled · safe rollout")+"\n"
                         "**Active home members:** "+str(stats["active"])+"\n"
                         "**Temporary visits:** "+str(stats["visiting"])+" (included in active)\n"
                         "**Pending cases:** "+str(stats["pending"])+"\n"
                         "**State transfers:** "+str(stats["transfers"])+"\n\n"
                         "Temporary visitors **remain active in their home alliance**. "
                         "API observations never change the managed roster."),
            color=theme.emColor1)
        await safe_edit_message(interaction,embed=embed,
                                view=TrackingView(self,interaction.user.id),content=None)

    async def show_cases(self,interaction,transfers_only=False):
        if not allowed(interaction):
            return await deny(interaction)
        ids,_=scope(interaction)
        if not ids:
            ids=[int(aid) for aid,_ in visible_alliances(interaction)]
        cases=await asyncio.to_thread(service.list_cases,ids)
        if transfers_only:
            cases=[c for c in cases if c["category"]=="state_transfer"]
        lines=[("#"+str(c["id"])+" · Chief "+str(c["fid"])+
                " · "+c["category"]+" · "+_preview(c["game_tag"] or "unknown"))
               for c in cases[:15]]
        embed=discord.Embed(title="📋 "+("State Transfer Reviews" if transfers_only else "Pending Member Actions"),
            description=("\n".join(lines) if lines else "No pending cases.")+
                        "\n\nUnapproved movements remain on the home roster until an authorized action.",
            color=theme.emColor1)
        await safe_edit_message(interaction,embed=embed,
                  view=CaseListView(self,interaction.user.id,cases,transfers_only),content=None)

    async def show_visits(self,interaction):
        if not allowed(interaction):
            return await deny(interaction)
        ids,_=scope(interaction)
        if not ids:
            ids=[int(aid) for aid,_ in visible_alliances(interaction)]
        visits=await asyncio.to_thread(service.list_visits,ids)
        lines=[("Chief "+str(v["fid"])+" · Home "+str(v["home_alliance"])+" → "+
                _preview(v["game_tag"] or v["game_alliance_id"])) for v in visits[:20]]
        embed=discord.Embed(title="🕒 Active Temporary Visits",
            description=("\n".join(lines) if lines else "No recorded temporary visitors.")+
                        "\n\nAll visitors remain active in their managed home roster.",
            color=theme.emColor1)
        await safe_edit_message(interaction,embed=embed,
                                view=Subview(self,interaction.user.id),content=None)

    async def show_history(self,interaction):
        if not allowed(interaction):
            return await deny(interaction)
        ids,_=scope(interaction)
        if not ids:
            ids=[int(aid) for aid,_ in visible_alliances(interaction)]
        history=await asyncio.to_thread(service.list_history,ids)
        lines=[("#"+str(c["id"])+" · Chief "+str(c["fid"])+" · "+
                str(c["decision"] or "resolved")+" · by "+str(c["decided_by"] or "system"))
               for c in history]
        embed=discord.Embed(title="📜 Membership Movement History",
            description=("\n".join(lines) if lines else "No resolved movement cases yet.")+
                        "\n\nAll decisions are preserved for audit, even when a member leaves.",
            color=theme.emColor1)
        await safe_edit_message(interaction,embed=embed,
                                view=Subview(self,interaction.user.id),content=None)

    async def show_registry(self,interaction):
        if not allowed(interaction):
            return await deny(interaction)
        ids,high=scope(interaction)
        if not ids:
            ids=[int(aid) for aid,_ in visible_alliances(interaction)]
        rules=await asyncio.to_thread(service.temporary_list,interaction.guild_id,ids,high)
        homes=[service.home_info(aid) for aid in ids]
        states=sorted({int(h[1]) for h in homes if h and h[1] is not None})
        lines=[("State "+str(r["state_id"])+" · "+_preview(r["tag"])+
                " · AID "+str(r["game_alliance_id"])+
                (" · State-wide" if r["scope_alliance_id"] is None else
                 " · Home "+str(r["scope_alliance_id"]))) for r in rules]
        embed=discord.Embed(title="🛡️ Temporary Alliance Registry",
             description=("\n".join(lines[:20]) if lines else "No approved temporary destinations.")+
                         "\n\nProvide the in-game **AID**, not a local WOSBOT alliance ID.",
             color=theme.emColor1)
        await safe_edit_message(interaction,embed=embed,
             view=RegistryView(self,interaction.user.id,rules,states,ids,high),content=None)

    async def show_settings(self,interaction):
        if not allowed(interaction):
            return await deny(interaction)
        enabled=await asyncio.to_thread(service.is_enabled,interaction.guild_id)
        embed=discord.Embed(title="⚙️ Membership Tracking Settings",
            description=("**Tracking:** "+("Enabled" if enabled else "Disabled")+"\n\n"
                         "Only Server Admin and above can enable monitoring.\n"
                         "First successful observation establishes each player's baseline.\n"
                         "Tracked movement opens review cases; it never automatically removes anyone.\n"
                         "Permanent roster changes require administrator confirmation "
                         "and are journaled with recovery snapshots."),
            color=theme.emColor1)
        await safe_edit_message(interaction,embed=embed,
                                view=SettingsView(self,interaction.user.id),content=None)


async def setup(bot):
    await bot.add_cog(MembershipTracking(bot))
