"""Alliance membership tracking menus; all callbacks enforce current guild scope."""
import asyncio
import logging
import discord
from discord.ext import commands

from . import membership_service as service
from .permission_handler import (PermissionManager, TIER_OWNER, TIER_GLOBAL, TIER_SERVER)
from .pimp_my_bot import theme, safe_edit_message, menu_timeout, confirm_timeout

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
        return PermissionManager.can_access_alliance(
            interaction.user.id, interaction.guild_id, alliance_id)
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

    @discord.ui.button(label="Sync Now", emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
    async def sync_now(self, interaction, button):
        await self.cog.open_sync(interaction)

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


class ReconcilePickView(discord.ui.View):
    def __init__(self,cog,owner,alliances):
        super().__init__(timeout=menu_timeout())
        self.cog,self.owner=cog,owner
        picker=discord.ui.Select(placeholder="Choose alliance to sync",
            options=[discord.SelectOption(label=name[:80],value=str(aid))
                     for aid,name in alliances[:25]],row=0)
        async def selected(interaction):
            if interaction.user.id!=owner or not allowed(interaction,server=True):
                return await deny(interaction)
            aid=int(picker.values[0])
            if aid not in {a for a,_ in visible_alliances(interaction)}:
                return await deny(interaction)
            await cog.queue_membership_sync(interaction,aid)
        picker.callback=selected
        self.add_item(picker)

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner or not allowed(interaction,server=True):
            await deny(interaction)
            return False
        return True

    @discord.ui.button(label="Back to Tracking",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,button):
        await self.cog.show_tracking(interaction)


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


class RegistryConfirmView(discord.ui.View):
    """User-bound confirmation for the AID resolved by the official API."""
    def __init__(self, cog, owner, guild_id, resolved, scope_alliance_id):
        super().__init__(timeout=confirm_timeout())
        self.cog, self.owner, self.guild_id = cog, owner, guild_id
        self.resolved, self.scope_alliance_id = resolved, scope_alliance_id

    async def interaction_check(self, interaction):
        if (interaction.user.id != self.owner or interaction.guild_id != self.guild_id
                or not allowed(interaction, self.scope_alliance_id,
                               server=self.scope_alliance_id is None)):
            await deny(interaction)
            return False
        return True

    @discord.ui.button(label="Confirm Temporary Alliance", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        state = self.resolved.state_id
        managed = visible_alliances(interaction)
        if self.scope_alliance_id is None:
            states = {int(info[1]) for aid, _ in managed
                      if (info := service.home_info(aid)) and info[1] is not None}
            if state not in states:
                return await deny(interaction)
        else:
            home = service.home_info(self.scope_alliance_id)
            if (not home or int(home[0]) != self.guild_id or
                    home[1] is None or int(home[1]) != state):
                return await deny(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(service.add_temporary, self.guild_id, state,
                                    self.resolved.aid, self.resolved.tag,
                                    interaction.user.id, self.scope_alliance_id)
        except (ValueError, TypeError, OSError) as exc:
            return await interaction.followup.send(
                "Unable to register this alliance: " + str(exc), ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.followup.send(
            "Registered **" + _preview(self.resolved.tag) + "** in State **" +
            str(state) + "** as " +
            ("state-wide" if self.scope_alliance_id is None else "home-alliance-only") +
            ". Home roster memberships remain unchanged.", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        for item in self.children:
            item.disabled = True
        await interaction.response.send_message(
            "Registration cancelled. Nothing was saved.", ephemeral=True)


class RegistryModal(discord.ui.Modal, title="Add Temporary Alliance"):
    def __init__(self, cog, state_id, alliance_id=None):
        super().__init__()
        self.cog, self.home_state, self.scope_alliance_id = cog, state_id, alliance_id
        self.state = discord.ui.TextInput(label="State ID", default=str(state_id), max_length=7)
        self.tag = discord.ui.TextInput(label="Alliance tag (case-sensitive)", max_length=20)
        self.add_item(self.state)
        self.add_item(self.tag)

    async def on_submit(self, interaction):
        if not allowed(interaction, self.scope_alliance_id,
                       server=self.scope_alliance_id is None):
            return await deny(interaction)
        try:
            state = int(self.state.value)
            if state <= 0:
                raise ValueError("Enter a valid state ID.")
            if self.scope_alliance_id is not None:
                home = service.home_info(self.scope_alliance_id)
                if not home or int(home[0]) != interaction.guild_id or int(home[1]) != state:
                    raise ValueError("The state must match your managed home alliance.")
            else:
                states = {int(info[1]) for aid, _ in visible_alliances(interaction)
                          if (info := service.home_info(aid)) and info[1] is not None}
                if state not in states:
                    raise ValueError("Select a state managed by this Discord server.")
        except (ValueError, TypeError) as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        from .membership_alliance_lookup import resolve
        resolved, error = await resolve(self.cog.bot, state, self.tag.value)
        if error:
            return await interaction.followup.send(error, ephemeral=True)
        embed = discord.Embed(
            title="Confirm Temporary Alliance",
            description=("**State:** " + str(resolved.state_id) +
                         "\n**Tag:** " + _preview(resolved.tag) +
                         "\n**Name:** " + _preview(resolved.name) +
                         "\n**Verified AID:** " + str(resolved.aid) +
                         "\n**Scope:** " +
                         ("State-wide" if self.scope_alliance_id is None
                          else "Home alliance " + str(self.scope_alliance_id)) +
                         "\n\nVisiting members stay active on their home roster."),
            color=theme.emColor1)
        await interaction.followup.send(
            embed=embed, view=RegistryConfirmView(
                self.cog, interaction.user.id, interaction.guild_id,
                resolved, self.scope_alliance_id), ephemeral=True)


class RuleScopePickView(Subview):
    def __init__(self,cog,owner,choices,state_wide):
        super().__init__(cog,owner)
        self.state_wide=state_wide
        options=[discord.SelectOption(
            label=(("State "+str(value)) if state_wide else name)[:100],
            value=str(value)) for value,name in choices[:25]]
        picker=discord.ui.Select(
            placeholder=("Choose state" if state_wide else "Choose home alliance"),
            options=options,row=0)
        async def select(interaction):
            aid=int(picker.values[0])
            if state_wide:
                if not allowed(interaction,server=True):
                    return await deny(interaction)
                current_states={int(row[1]) for _,_name in visible_alliances(interaction)
                                if (row:=service.home_info(_)) and row[1] is not None}
                if aid not in current_states:
                    return await deny(interaction)
                await interaction.response.send_modal(RegistryModal(cog,aid))
            else:
                if not allowed(interaction,aid):
                    return await deny(interaction)
                home=service.home_info(aid)
                if not home or home[1] is None:
                    return await interaction.response.send_message(
                        "This alliance has no home state set.",ephemeral=True)
                await interaction.response.send_modal(RegistryModal(cog,int(home[1]),aid))
        picker.callback=select
        self.add_item(picker)


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
        if len(self.states)==1:
            return await interaction.response.send_modal(RegistryModal(self.cog,self.states[0]))
        embed=discord.Embed(title="Choose State",
            description="Select a managed state for the temporary alliance rule.",
            color=theme.emColor1)
        await safe_edit_message(interaction,embed=embed,
            view=RuleScopePickView(self.cog,interaction.user.id,
                [(state,str(state)) for state in self.states],True),content=None)

    @discord.ui.button(label="Add for My Alliance", style=discord.ButtonStyle.secondary, row=0)
    async def add_scoped(self, interaction, button):
        if not self.own_alliances:
            return await deny(interaction)
        if len(self.own_alliances)>1:
            choices=[(aid,str(aid)) for aid in self.own_alliances]
            embed=discord.Embed(title="Choose Home Alliance",
                description="The temporary approval will apply to the selected home alliance only.",
                color=theme.emColor1)
            return await safe_edit_message(interaction,embed=embed,
                view=RuleScopePickView(self.cog,interaction.user.id,choices,False),content=None)
        aid=self.own_alliances[0]
        info=service.home_info(aid)
        if not info or info[1] is None:
            return await interaction.response.send_message("This alliance has no home state set.", ephemeral=True)
        await interaction.response.send_modal(RegistryModal(self.cog,int(info[1]),aid))



class ActionApprovalView(Subview):
    def __init__(self,cog,owner,case,action,destination=None):
        super().__init__(cog,owner)
        self.timeout=confirm_timeout()
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
        view=ActionApprovalView(cog,interaction.user.id,case,action,dest),content=None)


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
    def __init__(self,cog,owner,cases,transfers_only,page=0,has_next=False):
        super().__init__(cog,owner)
        self.transfers_only,self.page=transfers_only,page
        if page>0:
            prev=discord.ui.Button(label="Previous",style=discord.ButtonStyle.secondary,row=1)
            async def previous(interaction):
                await cog.show_cases(interaction,transfers_only,page-1)
            prev.callback=previous
            self.add_item(prev)
        if has_next:
            nxt=discord.ui.Button(label="Next",style=discord.ButtonStyle.secondary,row=1)
            async def following(interaction):
                await cog.show_cases(interaction,transfers_only,page+1)
            nxt.callback=following
            self.add_item(nxt)
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

    async def queue_membership_sync(self,interaction,alliance_id):
        if not allowed(interaction,alliance_id,server=True):
            return await deny(interaction)
        api=self.bot.get_cog("AllianceApiSync")
        if api is None:
            return await interaction.response.send_message(
                "Player Sync is not loaded.",ephemeral=True)
        result=api.queue_sync(alliance_id=alliance_id)
        labels={"queued":"Sync queued through Player Sync.",
                "busy":"A Player Sync run is already queued or active.",
                "unavailable":"The bot process queue is unavailable."}
        await interaction.response.send_message(labels.get(result,str(result)),ephemeral=True)

    async def open_sync(self,interaction):
        if not allowed(interaction,server=True):
            return await deny(interaction)
        allies=visible_alliances(interaction)
        if not allies:
            return await interaction.response.send_message("No managed alliances on this server.",ephemeral=True)
        if len(allies)==1:
            return await self.queue_membership_sync(interaction,allies[0][0])
        embed=discord.Embed(title="Sync Members",
            description="Choose a managed alliance to sync. This runs the existing Player Sync "
                        "and uses its API quota and request budget.",
            color=theme.emColor1)
        await safe_edit_message(interaction,embed=embed,
            view=ReconcilePickView(self,interaction.user.id,allies),content=None)

    async def show_tracking(self,interaction):
        if not allowed(interaction):
            return await deny(interaction)
        ids,_=scope(interaction)
        if not ids:
            ids=[int(aid) for aid,_ in visible_alliances(interaction)]
        stats=await asyncio.to_thread(service.dashboard,interaction.guild_id,ids)
        coverage=await asyncio.to_thread(service.tracking_coverage,ids)
        enabled=await asyncio.to_thread(service.is_enabled,interaction.guild_id)
        remaining=max(0,stats["active"]-coverage["observed"])
        status=("Monitoring enabled" if enabled else "OFF — enable in Tracking Settings")
        instructions=(
            "Tracking is OFF. The alliance's State Lock does not enable movement detection. "
            "Open Tracking Settings to enable it, then use Sync Now."
            if not enabled else
            ("No API membership observations yet. Use Sync Now to check your managed members."
             if coverage["observed"]==0 else
             ("Some members still need observations. Use Sync Now to check them."
              if remaining else
              "All currently active home members have stored membership observations."))
        )
        embed=discord.Embed(title="🛡️ Membership Tracking",
            description=("**Status:** "+status+"\n"
                         "**Active home members:** "+str(stats["active"])+"\n"
                         "**Members with observations:** "+str(coverage["observed"])+"\n"
                         "**Not yet observed:** "+str(remaining)+"\n"
                         "**Last API observation:** "+str(coverage["latest"] or "None")+"\n"
                         "**Temporary visits:** "+str(stats["visiting"])+" (included in active)\n"
                         "**Pending cases:** "+str(stats["pending"])+"\n"
                         "**State transfers:** "+str(stats["transfers"])+"\n\n"
                         +instructions+"\n\n"
                         "Temporary visitors **remain active in their home alliance**. "
                         "Observations never move or delete them."),
            color=theme.emColor1)
        await safe_edit_message(interaction,embed=embed,
                                view=TrackingView(self,interaction.user.id),content=None)

    async def show_cases(self,interaction,transfers_only=False,page=0):
        if not allowed(interaction):
            return await deny(interaction)
        ids,_=scope(interaction)
        if not ids:
            ids=[int(aid) for aid,_ in visible_alliances(interaction)]
        items=await asyncio.to_thread(service.list_cases,ids,limit=26,offset=page*25,
                                      category="state_transfer" if transfers_only else None)
        cases=items[:25]
        lines=[("#"+str(c["id"])+" · Chief "+str(c["fid"])+
                " · "+c["category"]+" · "+_preview(c["game_tag"] or "unknown"))
               for c in cases]
        embed=discord.Embed(title="📋 "+("State Transfer Reviews" if transfers_only else "Pending Member Actions"),
            description=("\n".join(lines) if lines else "No pending cases.")+
                        "\n\nPage "+str(page+1)+
                        " · Unapproved movements remain active on their home roster.",
            color=theme.emColor1)
        await safe_edit_message(interaction,embed=embed,
                  view=CaseListView(self,interaction.user.id,cases,transfers_only,page,len(items)>25),content=None)

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
        history=await asyncio.to_thread(service.list_history,ids,15)
        visits=await asyncio.to_thread(service.list_visit_history,ids,15)
        decisions=[("#"+str(c["id"])+" · Chief "+str(c["fid"])+" · "+
                    str(c["decision"] or "resolved")+" · by "+str(c["decided_by"] or "system"))
                   for c in history]
        timeline=[("Chief "+str(v["fid"])+" · "+_preview(v["game_tag"] or "Unknown")+
                   " · "+("Returned" if v["ended_at"] else "Visiting"))
                  for v in visits]
        embed=discord.Embed(title="📜 Membership Movement History",
            description=("**Resolved decisions**\n"+
                         ("\n".join(decisions) if decisions else "No decisions yet.")+
                         "\n\n**Temporary visit history**\n"+
                         ("\n".join(timeline) if timeline else "No recorded visits yet.")+
                         "\n\nAudit records remain available after a player leaves."),
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
                         "\n\nEnter only the state and alliance tag. WhiteoutStats resolves the AID automatically before confirmation.",
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
                         "The first successful observation checks each player's current state and alliance.\n"
                         "**After enabling:** run **Sync Now** in Membership Tracking.\n"
                         "State Lock controls registration; it does not activate tracking.\n"
                         "Detected movements open review cases without automatic removal.\n"
                         "Permanent roster changes require administrator confirmation "
                         "and are journaled with recovery snapshots."),
            color=theme.emColor1)
        await safe_edit_message(interaction,embed=embed,
                                view=SettingsView(self,interaction.user.id),content=None)


async def setup(bot):
    await bot.add_cog(MembershipTracking(bot))
