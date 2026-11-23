"""Admin-scoped reader for durable Player Sync field-change records."""
import io
import discord
from discord import app_commands
from discord.ext import commands
from . import player_sync_history
from .permission_handler import PermissionManager


def allowed_alliances(user_id,guild_id):
    ids,global_access=PermissionManager.get_admin_alliance_ids(user_id,guild_id)
    if global_access:
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect("db/alliance.sqlite",timeout=30)) as conn:
            rows=conn.execute("SELECT alliance_id FROM alliance_list WHERE discord_server_id=?",
                              (guild_id,)).fetchall()
        return [int(row[0]) for row in rows]
    return list(map(int,ids))


class SyncHistoryView(discord.ui.View):
    def __init__(self,cog,author_id,guild_id,page=0):
        super().__init__(timeout=300)
        self.cog=cog
        self.author_id=author_id
        self.guild_id=guild_id
        self.page=page

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.author_id or interaction.guild_id!=self.guild_id:
            await interaction.response.send_message("This history view belongs to another admin.",ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Previous",style=discord.ButtonStyle.secondary)
    async def prev(self,interaction,button):
        self.page=max(0,self.page-1)
        await self.cog.render(interaction,self)

    @discord.ui.button(label="Next",style=discord.ButtonStyle.secondary)
    async def next_page(self,interaction,button):
        self.page+=1
        await self.cog.render(interaction,self)

    @discord.ui.button(label="Export CSV",style=discord.ButtonStyle.primary)
    async def export(self,interaction,button):
        await self.cog.export_csv(interaction)


class SyncHistoryUI(commands.Cog):
    def __init__(self,bot):
        self.bot=bot

    async def render(self,interaction,view):
        ids=allowed_alliances(interaction.user.id,interaction.guild_id)
        if not ids:
            await interaction.response.send_message("No alliance history access.",ephemeral=True)
            return
        count,rows=player_sync_history.list_changes(ids,page=view.page,page_size=10)
        max_page=max(0,(count-1)//10)
        view.page=min(view.page,max_page)
        if view.page!=max_page:
            count,rows=player_sync_history.list_changes(ids,page=view.page,page_size=10)
        lines=[]
        for date,alliance,fid,name,field,old,new,run_id in rows:
            lines.append(f"**{name}** (`{fid}`) · alliance `{alliance}`\n"
                         f"{field}: `{str(old or '-')[:90]}` → `{str(new or '-')[:90]}`\n"
                         f"{date} UTC")
        embed=discord.Embed(title="Player Sync — Saved Change History",
            description="\n\n".join(lines)[:4000] if lines else "No recorded field changes yet.",
            color=discord.Color.blurple())
        embed.set_footer(text=f"Page {view.page+1}/{max_page+1} · {count} saved field changes")
        for component in view.children:
            if getattr(component,"label",None)=="Previous":
                component.disabled=view.page==0
            if getattr(component,"label",None)=="Next":
                component.disabled=view.page>=max_page
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed,view=view,ephemeral=True)
        else:
            await interaction.response.edit_message(embed=embed,view=view)

    async def export_csv(self,interaction):
        ids=allowed_alliances(interaction.user.id,interaction.guild_id)
        if not ids:
            await interaction.response.send_message("No alliance history access.",ephemeral=True);return
        data,count,truncated=player_sync_history.csv_bytes(ids)
        file=discord.File(io.BytesIO(data),filename="player_sync_history.csv")
        msg=f"Player Sync history export: {count} records."
        if truncated: msg+=" Export capped at 50,000 newest changes."
        await interaction.response.send_message(content=msg,file=file,ephemeral=True)

    @app_commands.command(name="sync_history",description="View your authorized alliance's saved Player Sync changes")
    @app_commands.guild_only()
    async def sync_history(self,interaction:discord.Interaction):
        if not allowed_alliances(interaction.user.id,interaction.guild_id):
            await interaction.response.send_message("Alliance admin access required.",ephemeral=True);return
        ids=allowed_alliances(interaction.user.id,interaction.guild_id)
        count,rows=player_sync_history.list_changes(ids,page=0,page_size=10)
        embed=discord.Embed(title="Player Sync — Saved Change History",color=discord.Color.blurple(),
            description="\n\n".join(
                f"**{name}** (`{fid}`) · alliance `{alliance}`\n"
                f"{field}: `{str(old or '-')[:90]}` → `{str(new or '-')[:90]}`\n{date} UTC"
                for date,alliance,fid,name,field,old,new,run_id in rows
            )[:4000] or "No recorded field changes yet.")
        embed.set_footer(text=f"Page 1/{max(1,(count+9)//10)} · {count} saved changes")
        await interaction.response.send_message(embed=embed,
            view=SyncHistoryView(self,interaction.user.id,interaction.guild_id),ephemeral=True)

    @app_commands.command(name="sync_history_export",description="Export authorized Player Sync field changes as CSV")
    @app_commands.guild_only()
    async def sync_history_export(self,interaction:discord.Interaction):
        await self.export_csv(interaction)


async def setup(bot):
    await bot.add_cog(SyncHistoryUI(bot))
