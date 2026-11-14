"""Manual alliance troop-quality tracker (T11/T12 with skill levels 1-3).

Data belongs to the Chief ID and follows transfers between alliances.
No WhiteoutStats field is used to infer troop research.
"""
import asyncio
import csv
import io
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands

from .permission_handler import PermissionManager
from .pimp_my_bot import safe_edit_message, menu_timeout

DB_PATH = Path("db/users.sqlite")
ALLIANCE_DB = Path("db/alliance.sqlite")
TROOPS = ("infantry", "lancer", "marksman")
# Integer troop tier and nullable skill are stored independently, with fixed valid combinations.
OPTIONS = (
    ("Unknown", (None, None)),
    ("Below T11", (0, None)),
    ("T11", (11, None)),
    ("T12 · Skill 1", (12, 1)),
    ("T12 · Skill 2", (12, 2)),
    ("T12 · Skill 3", (12, 3)),
)
CHOICES = {"unknown": (None, None), "below": (0, None), "t11": (11, None),
           "t12_1": (12, 1), "t12_2": (12, 2), "t12_3": (12, 3)}
PAGE_SIZE = 15


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def initialize(path=DB_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        db.execute("PRAGMA busy_timeout=30000")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS member_troop_quality (
                fid INTEGER PRIMARY KEY,
                infantry_tier INTEGER,
                infantry_skill INTEGER,
                lancer_tier INTEGER,
                lancer_skill INTEGER,
                marksman_tier INTEGER,
                marksman_skill INTEGER,
                updated_at TEXT NOT NULL,
                updated_by INTEGER NOT NULL,
                CHECK(infantry_tier IS NULL OR infantry_tier IN (0, 11, 12)),
                CHECK(lancer_tier IS NULL OR lancer_tier IN (0, 11, 12)),
                CHECK(marksman_tier IS NULL OR marksman_tier IN (0, 11, 12)),
                CHECK((infantry_tier = 12 AND infantry_skill BETWEEN 1 AND 3)
                      OR (infantry_tier IS NOT 12 AND infantry_skill IS NULL)),
                CHECK((lancer_tier = 12 AND lancer_skill BETWEEN 1 AND 3)
                      OR (lancer_tier IS NOT 12 AND lancer_skill IS NULL)),
                CHECK((marksman_tier = 12 AND marksman_skill BETWEEN 1 AND 3)
                      OR (marksman_tier IS NOT 12 AND marksman_skill IS NULL))
            );
            CREATE TABLE IF NOT EXISTS member_troop_quality_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fid INTEGER NOT NULL,
                alliance_id INTEGER NOT NULL,
                troop_type TEXT NOT NULL,
                old_tier INTEGER,
                old_skill INTEGER,
                new_tier INTEGER,
                new_skill INTEGER,
                changed_at TEXT NOT NULL,
                changed_by INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_troop_quality_history
            ON member_troop_quality_history(fid, changed_at DESC);
        """)


def read_members(alliance_id, path=DB_PATH):
    with closing(sqlite3.connect(path, timeout=30)) as db:
        rows = db.execute(
            "SELECT fid, COALESCE(nickname, '') FROM users "
            "WHERE alliance=? ORDER BY LOWER(nickname), fid", (str(alliance_id),)
        ).fetchall()
    return [(int(fid), name) for fid, name in rows]


def read_quality(fids, path=DB_PATH):
    if not fids:
        return {}
    initialize(path)
    result = {}
    with closing(sqlite3.connect(path, timeout=30)) as db:
        for start in range(0, len(fids), 500):
            batch = [int(fid) for fid in fids[start:start + 500]]
            holders = ",".join("?" for _ in batch)
            rows = db.execute(
                "SELECT fid, infantry_tier, infantry_skill, lancer_tier, lancer_skill, "
                "marksman_tier, marksman_skill FROM member_troop_quality "
                f"WHERE fid IN ({holders})", batch
            ).fetchall()
            for row in rows:
                result[row[0]] = {troop: (row[1 + 2*i], row[2 + 2*i])
                                  for i, troop in enumerate(TROOPS)}
    return result


def format_tier(tier, skill):
    if tier == 12 and skill in (1, 2, 3):
        return f"T12 · Skill {skill}/3"
    if tier == 11:
        return "T11"
    if tier == 0:
        return "Below T11"
    return "Unknown"


def format_member_line(fid, name, data):
    return (f"**{discord.utils.escape_markdown((name or str(fid))[:80])}** · ID: \`{fid}\`\n"
            + "  ·  ".join(f"{t.title()}: {format_tier(*data.get(t, (None, None)))}"
                         for t in TROOPS))


def save_quality(alliance_id, fids, changes, editor_id, path=DB_PATH):
    """One atomic bulk transaction; require every target still belongs to the alliance."""
    if not fids or not isinstance(changes, dict) or not changes:
        raise ValueError("Select members and troop values to save.")
    if len(fids) != len(set(fids)):
        raise ValueError("Duplicate Chief IDs.")
    if set(changes) - set(TROOPS):
        raise ValueError("Unknown troop type.")
    for pair in changes.values():
        if pair not in CHOICES.values():
            raise ValueError("Invalid troop tier or T12 skill.")
    initialize(path)
    when = utc_now()
    changed = 0
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            ids = [int(fid) for fid in fids]
            for start in range(0, len(ids), 500):
                batch = ids[start:start + 500]
                marks = ",".join("?" for _ in batch)
                found = {int(row[0]) for row in db.execute(
                    f"SELECT fid FROM users WHERE alliance=? AND fid IN ({marks})",
                    [str(alliance_id), *batch]
                ).fetchall()}
                if found != set(batch):
                    raise ValueError("Member selection changed. Reopen the member list.")
            for fid in ids:
                row = db.execute(
                    "SELECT infantry_tier, infantry_skill, lancer_tier, lancer_skill, "
                    "marksman_tier, marksman_skill FROM member_troop_quality WHERE fid=?",
                    (fid,)
                ).fetchone()
                old = {t: (row[2*i], row[2*i+1]) if row else (None, None)
                       for i, t in enumerate(TROOPS)}
                new = dict(old)
                for t, pair in changes.items():
                    if old[t] == pair:
                        continue
                    new[t] = pair
                    db.execute(
                        "INSERT INTO member_troop_quality_history "
                        "(fid, alliance_id, troop_type, old_tier, old_skill, "
                        "new_tier, new_skill, changed_at, changed_by) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (fid, int(alliance_id), t, *old[t], *pair, when, int(editor_id)),
                    )
                    changed += 1
                if new != old:
                    db.execute(
                        "INSERT INTO member_troop_quality "
                        "(fid, infantry_tier, infantry_skill, lancer_tier, lancer_skill, "
                        "marksman_tier, marksman_skill, updated_at, updated_by) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(fid) DO UPDATE SET "
                        "infantry_tier=excluded.infantry_tier, infantry_skill=excluded.infantry_skill, "
                        "lancer_tier=excluded.lancer_tier, lancer_skill=excluded.lancer_skill, "
                        "marksman_tier=excluded.marksman_tier, marksman_skill=excluded.marksman_skill, "
                        "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                        (fid, *new["infantry"], *new["lancer"], *new["marksman"],
                         when, int(editor_id)),
                    )
            db.commit()
        except Exception:
            db.rollback()
            raise
    return changed


def export_csv(alliance_id, path=DB_PATH):
    members = read_members(alliance_id, path)
    quality = read_quality([fid for fid, _ in members], path)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Member", "Chief ID", "Infantry Tier", "Infantry T12 Skill",
                "Lancer Tier", "Lancer T12 Skill", "Marksman Tier", "Marksman T12 Skill"])
    for fid, name in members:
        q = quality.get(fid, {})
        w.writerow([name, fid, *(v for t in TROOPS for v in q.get(t, (None, None)))])
    return out.getvalue().encode("utf-8-sig")


def alliance_name(alliance_id):
    with closing(sqlite3.connect(ALLIANCE_DB, timeout=30)) as db:
        row = db.execute("SELECT name FROM alliance_list WHERE alliance_id=?", (alliance_id,)).fetchone()
    return row[0] if row else None


def allowed(user_id, guild_id, alliance_id):
    ids, global_admin = PermissionManager.get_admin_alliance_ids(user_id, guild_id)
    return global_admin or str(alliance_id) in {str(x) for x in ids}


class QualityView(discord.ui.View):
    def __init__(self, cog, alliance_id, name, owner):
        super().__init__(timeout=menu_timeout())
        self.cog, self.alliance_id, self.name, self.owner = cog, alliance_id, name, owner

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner or not await asyncio.to_thread(
            allowed, interaction.user.id, interaction.guild_id, self.alliance_id
        ):
            await interaction.response.send_message("You cannot manage this alliance.", ephemeral=True)
            return False
        return True

    async def go_dashboard(self, interaction):
        await self.cog.show_troop_quality_for(interaction, self.alliance_id)


class Dashboard(QualityView):
    def __init__(self, cog, alliance_id, name, owner, members, count):
        super().__init__(cog, alliance_id, name, owner)
        self.members = members
        self.count = count

    @discord.ui.button(label="Individual Edit", emoji="✏️", style=discord.ButtonStyle.primary)
    async def individual(self, interaction, button):
        await safe_edit_message(interaction,
            embed=member_embed(self.name, "Select a member to edit"),
            view=MemberPicker(self.cog, self.alliance_id, self.name, self.owner, False))

    @discord.ui.button(label="Bulk Edit", emoji="👥", style=discord.ButtonStyle.primary)
    async def bulk(self, interaction, button):
        await safe_edit_message(interaction,
            embed=member_embed(self.name, "Select multiple members to update"),
            view=MemberPicker(self.cog, self.alliance_id, self.name, self.owner, True))

    @discord.ui.button(label="Member Preview", emoji="📋", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction, button):
        view=Preview(self.cog, self.alliance_id, self.name, self.owner)
        await safe_edit_message(interaction, embed=view.make_embed(), view=view)

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        menu = self.cog.bot.get_cog("MainMenu")
        if menu is None:
            await interaction.response.send_message("Alliance menu unavailable.", ephemeral=True)
            return
        await menu.show_alliance_hub(interaction, self.alliance_id)


def member_embed(name, subtitle):
    return discord.Embed(title=f"🛡️ Troop Quality · {name}", description=subtitle)


class SearchModal(discord.ui.Modal, title="Find a member"):
    query = discord.ui.TextInput(label="Name or Chief ID", required=False, max_length=100)
    def __init__(self, view):
        super().__init__()
        self.target = view

    async def on_submit(self, interaction):
        self.target.query_text = str(self.query.value).strip().casefold()
        self.target.page = 0
        await safe_edit_message(interaction, embed=self.target.make_embed(), view=self.target)


class MemberPicker(QualityView):
    def __init__(self, cog, alliance_id, name, owner, bulk):
        super().__init__(cog, alliance_id, name, owner)
        self.bulk = bulk
        self.query_text = ""
        self.page = 0
        self.selected = set()
        self.rebuild()

    def filtered(self):
        members = read_members(self.alliance_id)
        return [(fid, name) for fid, name in members
                if self.query_text in name.casefold() or self.query_text in str(fid)]

    def make_embed(self):
        members = self.filtered()
        page = members[self.page*PAGE_SIZE:(self.page+1)*PAGE_SIZE]
        embed=member_embed(self.name, ("**Bulk Edit** · " if self.bulk else "**Individual Edit** · ")
                 + f"{len(members)} matching members · Page {self.page+1}/{max(1,(len(members)+PAGE_SIZE-1)//PAGE_SIZE)}"
                 + (f"\n**Selected:** {len(self.selected)}" if self.bulk else ""))
        embed.description += "\n" + ("\n".join(f"• {discord.utils.escape_markdown(name or str(fid))} · \`{fid}\`"
                                   for fid,name in page) or "No matching members.")
        return embed

    def rebuild(self):
        self.clear_items()
        members=self.filtered()
        maxpages=max(1,(len(members)+PAGE_SIZE-1)//PAGE_SIZE)
        self.page=max(0,min(self.page,maxpages-1))
        page=members[self.page*PAGE_SIZE:(self.page+1)*PAGE_SIZE]
        if page:
            menu=discord.ui.Select(
                placeholder="Select members on this page" if self.bulk else "Choose one member",
                min_values=0 if self.bulk else 1,
                max_values=len(page) if self.bulk else 1,
                options=[discord.SelectOption(label=(name or str(fid))[:100], value=str(fid),
                                              description=f"Chief ID: {fid}",
                                              default=self.bulk and fid in self.selected)
                         for fid,name in page], row=0)
            async def chosen(interaction):
                if self.bulk:
                    visible={fid for fid,_ in page}
                    self.selected.difference_update(visible)
                    self.selected.update(int(x) for x in menu.values)
                    self.rebuild()
                    await safe_edit_message(interaction,embed=self.make_embed(),view=self)
                else:
                    fid=int(menu.values[0])
                    await safe_edit_message(interaction,
                        embed=editor_embed(self.name, [fid], {}, read_quality([fid]).get(fid, {})),
                        view=Editor(self.cog,self.alliance_id,self.name,self.owner,[fid],False))
            menu.callback=chosen
            self.add_item(menu)
        for label,emoji,delta,disabled in (("Previous","◀️",-1,self.page==0),
                                           ("Next","▶️",1,self.page>=maxpages-1)):
            b=discord.ui.Button(label=label,emoji=emoji,disabled=disabled,row=1)
            async def paginate(interaction, d=delta):
                self.page+=d
                self.rebuild()
                await safe_edit_message(interaction,embed=self.make_embed(),view=self)
            b.callback=paginate
            self.add_item(b)
        find=discord.ui.Button(label="Search",emoji="🔎",row=2)
        async def search(interaction):
            await interaction.response.send_modal(SearchModal(self))
        find.callback=search
        self.add_item(find)
        if self.bulk:
            edit=discord.ui.Button(label=f"Edit Selected ({len(self.selected)})",
                                   style=discord.ButtonStyle.success,
                                   disabled=not self.selected,row=2)
            async def edit_selected(interaction):
                ids=sorted(self.selected)
                await safe_edit_message(interaction,
                    embed=editor_embed(self.name,ids,{},{}),
                    view=Editor(self.cog,self.alliance_id,self.name,self.owner,ids,True))
            edit.callback=edit_selected
            self.add_item(edit)
        back=discord.ui.Button(label="Back",emoji="⬅️",row=3)
        async def go_back(interaction):
            await self.go_dashboard(interaction)
        back.callback=go_back
        self.add_item(back)


def editor_embed(name,fids,changes,prior):
    embed=discord.Embed(title=f"🛡️ Troop Quality · {name}",
                        description=(f"Editing **{len(fids)}** member(s)\n"
                         + (f"Chief ID: \`{fids[0]}\`\n" if len(fids)==1 else "")
                         + "Choose each troop's highest tier and T12 skill.\n"
                         + ("**Unchanged** leaves bulk records untouched.\n" if len(fids)>1 else "")))
    for troop in TROOPS:
        pair=changes.get(troop, prior.get(troop,(None,None)))
        embed.add_field(name=troop.title(),value=format_tier(*pair),inline=True)
    return embed


class Editor(QualityView):
    def __init__(self,cog,alliance_id,name,owner,fids,bulk):
        super().__init__(cog,alliance_id,name,owner)
        self.fids=fids
        self.bulk=bulk
        self.prior=read_quality(fids).get(fids[0],{}) if not bulk else {}
        self.draft={} if bulk else {t:self.prior.get(t,(None,None)) for t in TROOPS}
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        for idx,troop in enumerate(TROOPS):
            opts=([discord.SelectOption(label="Unchanged",value="unchanged",
                         default=troop not in self.draft)] if self.bulk else [])
            for label,pair in OPTIONS:
                key=next(k for k,v in CHOICES.items() if pair==v)
                opts.append(discord.SelectOption(label=label,value=key,
                    default=self.draft.get(troop)==pair))
            select=discord.ui.Select(placeholder=f"{troop.title()} tier / T12 skill",
                                     options=opts,row=idx)
            async def pick(interaction, name=troop, widget=select):
                choice=widget.values[0]
                if choice=="unchanged":
                    self.draft.pop(name,None)
                else:
                    self.draft[name]=CHOICES[choice]
                self.rebuild()
                await safe_edit_message(interaction,
                    embed=editor_embed(self.name,self.fids,self.draft,
                                       {} if self.bulk else self.prior),view=self)
            select.callback=pick
            self.add_item(select)
        save=discord.ui.Button(label="Review & Save",emoji="💾",
            style=discord.ButtonStyle.success,disabled=not self.draft,row=3)
        async def review(interaction):
            changes=dict(self.draft)
            desc=f"**{len(self.fids)}** member(s) will be updated.\n"
            desc+="\n".join(f"• {t.title()}: {format_tier(*v)}" for t,v in changes.items())
            desc+="\n\nUnselected troop types will not change. Confirm?"
            await safe_edit_message(interaction,
                embed=discord.Embed(title="Confirm Troop Quality Changes",description=desc),
                view=ConfirmSave(self.cog,self.alliance_id,self.name,self.owner,self.fids,changes,self.bulk))
        save.callback=review
        self.add_item(save)
        back=discord.ui.Button(label="Back",emoji="⬅️",row=3)
        async def go_back(interaction):
            await safe_edit_message(interaction,
                embed=member_embed(self.name,"Select members"),
                view=MemberPicker(self.cog,self.alliance_id,self.name,self.owner,self.bulk))
        back.callback=go_back
        self.add_item(back)


class ConfirmSave(QualityView):
    def __init__(self,cog,alliance_id,name,owner,fids,changes,bulk):
        super().__init__(cog,alliance_id,name,owner)
        self.fids,self.changes,self.bulk=fids,changes,bulk

    @discord.ui.button(label="Confirm Save",emoji="✅",style=discord.ButtonStyle.success)
    async def save(self,interaction,button):
        await interaction.response.defer()
        try:
            count=await asyncio.to_thread(save_quality,self.alliance_id,self.fids,
                                          self.changes,interaction.user.id)
        except (sqlite3.Error,ValueError) as exc:
            await interaction.followup.send(f"Could not save troop quality: {exc}",ephemeral=True)
            return
        view=Preview(self.cog,self.alliance_id,self.name,self.owner)
        await interaction.edit_original_response(
            embed=view.make_embed(extra=f"✅ Saved {count} troop changes for {len(self.fids)} member(s)."),
            view=view)

    @discord.ui.button(label="Cancel",emoji="⬅️",style=discord.ButtonStyle.secondary)
    async def cancel(self,interaction,button):
        view=Editor(self.cog,self.alliance_id,self.name,self.owner,self.fids,self.bulk)
        view.draft=dict(self.changes)
        view.rebuild()
        await safe_edit_message(interaction,
            embed=editor_embed(self.name,self.fids,view.draft,view.prior),view=view)


class Preview(QualityView):
    def __init__(self,cog,alliance_id,name,owner):
        super().__init__(cog,alliance_id,name,owner)
        self.page=0
        self.filter="all"
        self.rebuild()

    def rows(self):
        members=read_members(self.alliance_id)
        quality=read_quality([fid for fid,_ in members])
        filtered=[]
        for fid,name in members:
            q=quality.get(fid,{})
            values=[q.get(t,(None,None)) for t in TROOPS]
            if self.filter=="t12" and not any(v[0]==12 for v in values):continue
            if self.filter=="t11" and not any(v[0]==11 for v in values):continue
            if self.filter=="unknown" and all(v[0] is not None for v in values):continue
            filtered.append((fid,name,q))
        return filtered

    def make_embed(self,extra=""):
        rows=self.rows()
        pages=max(1,(len(rows)+9)//10)
        self.page=max(0,min(self.page,pages-1))
        subset=rows[self.page*10:(self.page+1)*10]
        description=f"**{len(rows)}** matching members · Page {self.page+1}/{pages}"
        if extra:description=f"{extra}\n\n{description}"
        description+="\n\n"+("\n\n".join(format_member_line(fid,name,q) for fid,name,q in subset)
                             or "No members found.")
        embed=discord.Embed(title=f"🛡️ Troop Quality Preview · {self.name}",
                            description=description[:4000])
        embed.set_footer(text="T12 skill 1–3 · Unrecorded tiers show Unknown · Stored times use UTC")
        return embed

    def rebuild(self):
        self.clear_items()
        flt=discord.ui.Select(placeholder="Filter members",
            options=[discord.SelectOption(label=title,value=val,default=self.filter==val)
                     for title,val in (("All","all"),("Has T11","t11"),
                                       ("Has T12","t12"),("Has Unknown","unknown"))],
            row=0)
        async def change_filter(interaction):
            self.filter=flt.values[0]
            self.page=0
            self.rebuild()
            await safe_edit_message(interaction,embed=self.make_embed(),view=self)
        flt.callback=change_filter
        self.add_item(flt)
        for label,emoji,delta in (("Previous","◀️",-1),("Next","▶️",1)):
            btn=discord.ui.Button(label=label,emoji=emoji,row=1)
            async def page_change(interaction,d=delta):
                self.page=max(0,self.page+d)
                self.rebuild()
                await safe_edit_message(interaction,embed=self.make_embed(),view=self)
            btn.callback=page_change
            self.add_item(btn)
        export=discord.ui.Button(label="Export CSV",emoji="📄",row=2)
        async def send_csv(interaction):
            await interaction.response.defer(ephemeral=True)
            content=await asyncio.to_thread(export_csv,self.alliance_id)
            await interaction.followup.send(
                file=discord.File(io.BytesIO(content),
                    filename=f"troop_quality_alliance_{self.alliance_id}.csv"),
                ephemeral=True)
        export.callback=send_csv
        self.add_item(export)
        back=discord.ui.Button(label="Back",emoji="⬅️",row=2)
        async def go_back(interaction):await self.go_dashboard(interaction)
        back.callback=go_back
        self.add_item(back)


class TroopQuality(commands.Cog):
    def __init__(self,bot):
        self.bot=bot

    async def show_troop_quality_for(self,interaction,alliance_id):
        if not await asyncio.to_thread(allowed,interaction.user.id,interaction.guild_id,alliance_id):
            await interaction.response.send_message("You do not have permission to manage this alliance.",ephemeral=True)
            return
        name=await asyncio.to_thread(alliance_name,alliance_id)
        if name is None:
            await interaction.response.send_message("Alliance not found.",ephemeral=True)
            return
        members=await asyncio.to_thread(read_members,alliance_id)
        entries=await asyncio.to_thread(read_quality,[fid for fid,_ in members])
        recorded=sum(bool(entries.get(fid)) for fid,_ in members)
        embed=discord.Embed(title=f"🛡️ Troop Quality · {name}",
            description=(f"**{len(members)} members** · **{recorded} recorded** · "
                         f"**{len(members)-recorded} pending**\n\n"
                         "Select an action:\n"
                         "✏️ **Individual Edit** — one member, three troop types\n"
                         "👥 **Bulk Edit** — multiple members, unchanged fields preserved\n"
                         "📋 **Member Preview** — names, Chief IDs, troop tiers and skill levels"))
        await safe_edit_message(interaction,embed=embed,
            view=Dashboard(self,alliance_id,name,interaction.user.id,members,recorded))


async def setup(bot):
    await bot.add_cog(TroopQuality(bot))
