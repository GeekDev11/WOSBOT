"""Self-service minister bookings using the existing appointments table.

The current minister schedule has ONE active dated occurrence per minister type.
No legacy appointment is silently deleted or assigned a date.
"""
import asyncio
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from discord import app_commands
import discord
from discord.ext import commands, tasks
from .permission_handler import PermissionManager
from .pimp_my_bot import menu_timeout

DB = "db/svs.sqlite"
USERS_DB = "db/users.sqlite"
TYPES = ("Construction Day", "Research Day", "Troops Training Day")
UTC = timezone.utc


def connect():
    db = sqlite3.connect(DB, timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA journal_mode=WAL")
    db.row_factory = sqlite3.Row
    return db


def migrate(db):
    db.execute("""CREATE TABLE IF NOT EXISTS minister_event_dates (
        appointment_type TEXT PRIMARY KEY,
        event_date TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0,
        updated_by INTEGER NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS minister_reminder_channels (
        guild_id INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS minister_player_approvals (
        fid INTEGER PRIMARY KEY,
        discord_id INTEGER NOT NULL,
        guild_id INTEGER NOT NULL,
        approved_by INTEGER NOT NULL,
        approved_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS minister_booking_owners (
        fid INTEGER NOT NULL,
        appointment_type TEXT NOT NULL,
        discord_id INTEGER NOT NULL,
        PRIMARY KEY(fid,appointment_type)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS minister_reminder_deliveries (
        appointment_type TEXT NOT NULL,
        event_date TEXT NOT NULL,
        fid INTEGER NOT NULL,
        booking_time TEXT NOT NULL,
        lead_minutes INTEGER NOT NULL,
        discord_id INTEGER NOT NULL,
        delivered_at TEXT NOT NULL,
        PRIMARY KEY(appointment_type,event_date,fid,booking_time,lead_minutes)
    )""")
    # Detect legacy double-booked slots before enabling new reservations.
    duplicate=db.execute("SELECT 1 FROM appointments GROUP BY appointment_type,time "
                         "HAVING COUNT(*)>1 LIMIT 1").fetchone()
    if not duplicate:
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS minister_unique_occupied_time "
                   "ON appointments(appointment_type,time)")
    db.commit()


def active_events(db):
    rows = db.execute(
        "SELECT appointment_type,event_date FROM minister_event_dates WHERE enabled=1"
    ).fetchall()
    return {r["appointment_type"]:r["event_date"] for r in rows}


def slots(mode=0):
    if mode==0:
        return [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0,30)]
    # Compatibility with MinisterSchedule.get_time_slots(1).
    return ["00:00"]+[f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (15,45)]


def configured_slots(db):
    try:
        row=db.execute("SELECT context_id FROM reference WHERE context='slot_mode'").fetchone()
        mode=int(row[0]) if row else 0
    except sqlite3.OperationalError:
        mode=0
    return slots(mode)


def registered_fids(discord_id, guild_id):
    with closing(sqlite3.connect(USERS_DB, timeout=30)) as db:
        return db.execute(
            "SELECT fid,nickname,alliance FROM users WHERE discord_id=? AND discord_server_id=?",
            (discord_id,guild_id)
        ).fetchall()


def permitted_fids(discord_id, guild_id):
    rows = registered_fids(discord_id,guild_id)
    with closing(connect()) as db:
        migrate(db)
        approved = {
            r["fid"] for r in db.execute(
                "SELECT fid FROM minister_player_approvals WHERE discord_id=? AND guild_id=?",
                (discord_id,guild_id)
            ).fetchall()
        }
    return [(int(fid),name,aid) for fid,name,aid in rows if int(fid) in approved]


def book_many(discord_id, guild_id, fid, selections):
    """All-or-nothing new booking or own-slot movement, shared legacy table."""
    if not selections or any(t not in TYPES for t in selections):
        raise ValueError("Invalid appointment selection.")
    if fid not in {row[0] for row in permitted_fids(discord_id,guild_id)}:
        raise PermissionError("Your character is not approved for minister reservations.")
    with closing(sqlite3.connect(USERS_DB, timeout=30)) as users:
        row = users.execute(
            "SELECT alliance FROM users WHERE fid=? AND discord_id=? AND discord_server_id=?",
            (fid,discord_id,guild_id)
        ).fetchone()
        if row is None:
            raise PermissionError("This character is no longer linked to you in this server.")
        alliance = row[0]
    with closing(connect()) as db:
        migrate(db)
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='index' "
                          "AND name='minister_unique_occupied_time'").fetchone():
            raise ValueError("An admin must resolve existing duplicate slots before member booking.")
        events = active_events(db)
        valid_times=set(configured_slots(db))
        if any(t not in valid_times for t in selections.values()):
            raise ValueError("Selected time is not valid in the current scheduling mode.")
        if not db.execute("SELECT 1 FROM minister_reminder_channels WHERE guild_id=?",
                          (guild_id,)).fetchone():
            raise ValueError("An administrator must configure the member reminder channel first.")
        now = datetime.now(UTC)
        previous = {}
        # A player cannot hold two minister types at the same UTC date/time.
        existing=db.execute("SELECT appointment_type,time FROM appointments WHERE fid=?",
                            (fid,)).fetchall()
        for booked in existing:
            other=booked["appointment_type"]
            if other not in selections:
                for selected_type,selected_time in selections.items():
                    if (events.get(other)==events.get(selected_type)
                            and booked["time"]==selected_time):
                        raise ValueError("These appointments overlap on the same day.")
        for typ,time in selections.items():
            if typ not in events:
                raise ValueError(f"{typ} reservations are not open.")
            start = datetime.fromisoformat(events[typ]+"T"+time+"+00:00")
            # Both mandatory reminders must be schedulable before confirmation.
            if start <= now+timedelta(minutes=5):
                raise ValueError(f"{typ} requires booking more than 5 minutes before its start.")
            occupied = db.execute(
                "SELECT fid FROM appointments WHERE appointment_type=? AND time=? AND fid!=?",
                (typ,time,fid)
            ).fetchone()
            if occupied:
                raise ValueError(f"{typ} at {time} UTC has already been booked.")
            current = db.execute(
                "SELECT time FROM appointments WHERE fid=? AND appointment_type=?",
                (fid,typ)
            ).fetchone()
            previous[typ]=current["time"] if current else None
            if current:
                owner = db.execute(
                    "SELECT discord_id FROM minister_booking_owners WHERE fid=? AND appointment_type=?",
                    (fid,typ)
                ).fetchone()
                # Existing admin-created bookings may predate reminder ownership
                # snapshots. The approved/linked FID may safely adopt its own row.
                if owner is not None and int(owner["discord_id"]) != discord_id:
                    raise PermissionError(f"{typ} belongs to a different registered Discord member.")
        for first,first_time in selections.items():
            for second,second_time in selections.items():
                if first<second and events[first]==events[second] and first_time==second_time:
                    raise ValueError("Two selected appointments overlap on the same day.")
        for typ,time in selections.items():
            db.execute("DELETE FROM appointments WHERE fid=? AND appointment_type=?", (fid,typ))
            db.execute(
                "INSERT INTO appointments(fid,appointment_type,time,alliance) VALUES(?,?,?,?)",
                (fid,typ,time,alliance)
            )
            db.execute(
                "INSERT INTO minister_booking_owners(fid,appointment_type,discord_id) VALUES(?,?,?) "
                "ON CONFLICT(fid,appointment_type) DO UPDATE SET discord_id=excluded.discord_id",
                (fid,typ,discord_id)
            )
        db.commit()
    return previous


def snapshot_owner(fid, appointment_type):
    """Capture the associated Discord user for a legacy admin booking.

    Does not infer ownership later from changes to the player's registration.
    A player without a Discord link cannot be individually tagged.
    """
    with closing(sqlite3.connect(USERS_DB,timeout=30)) as db:
        row=db.execute("SELECT discord_id FROM users WHERE fid=?",(fid,)).fetchone()
    if not row or row[0] is None:
        return False
    with closing(connect()) as db:
        migrate(db)
        db.execute("INSERT INTO minister_booking_owners(fid,appointment_type,discord_id) "
                   "VALUES(?,?,?) ON CONFLICT(fid,appointment_type) "
                   "DO UPDATE SET discord_id=excluded.discord_id",
                   (fid,appointment_type,int(row[0])))
        db.commit()
    return True


class TypePicker(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="Choose 1, 2, or all 3 ministers", min_values=1,max_values=3,
                         options=[discord.SelectOption(label=t,value=t) for t in TYPES])
    async def callback(self, interaction):
        view=self.view
        view.types=list(self.values)
        view.index=0
        view.page=0
        view.render()
        await interaction.response.edit_message(embed=view.embed(),view=view)


class TimePicker(discord.ui.Select):
    def __init__(self, times):
        super().__init__(placeholder="Select your available UTC time",min_values=1,max_values=1,
                         options=[discord.SelectOption(label=t+" UTC",value=t) for t in times])
    async def callback(self, interaction):
        view=self.view
        typ=view.types[view.index]
        view.choices[typ]=self.values[0]
        if view.same_for_all and view.index==0:
            for other in view.types[1:]:
                view.choices[other]=self.values[0]
            view.index=len(view.types)
        else:
            view.index+=1
        view.page=0
        view.render()
        await interaction.response.edit_message(embed=view.embed(),view=view)


class BookingWizard(discord.ui.View):
    def __init__(self, user_id, guild_id, fid, bot):
        super().__init__(timeout=menu_timeout())
        self.user_id=user_id
        self.bot=bot
        self.guild_id=guild_id
        self.fid=fid
        self.types=[]
        self.index=0
        self.page=0
        self.choices={}
        self.same_for_all=False
        self.render()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id or interaction.guild_id != self.guild_id:
            await interaction.response.send_message("This menu belongs to another member.",ephemeral=True)
            return False
        return True

    def embed(self):
        with closing(connect()) as db:
            dates=active_events(db)
            existing=db.execute(
                "SELECT appointment_type,time FROM appointments WHERE fid=?",
                (self.fid,)
            ).fetchall()
        previously={row["appointment_type"]:row["time"] for row in existing}
        if not self.types:
            date_info="\n".join(f"• {t}: {day} UTC" for t,day in dates.items())
            description="Choose 1–3 minister appointments in one session.\n\n**Open dates:**\n"+(date_info or "None configured.")
            if previously:
                description+="\n\n**Your current bookings:**\n"+"\n".join(
                    f"• {t}: {time} UTC" for t,time in previously.items()
                )
        elif self.index<len(self.types):
            typ=self.types[self.index]
            description=f"Choose a time for **{typ}** on {dates.get(typ,'unavailable')} UTC.\nPage {self.page+1}/{self.total_pages()}."
            if self.index==0 and len(self.types)>1:
                description+="\nUse the Same time for all toggle if preferred."
        else:
            description="**Review your choices**\n"+"\n".join(
                f"• {t}: {dates.get(t,'date missing')} at {v} UTC" for t,v in self.choices.items()
            )+"\n\nConfirm once to reserve all. If any slot fails, nothing changes."
        return discord.Embed(title="Minister Reservations",description=description,color=discord.Color.blurple())

    def total_pages(self):
        with closing(connect()) as db:
            return max(1,(len(configured_slots(db))+23)//24)

    def render(self):
        self.clear_items()
        if not self.types:
            self.add_item(TypePicker())
            return
        if self.index<len(self.types):
            typ=self.types[self.index]
            with closing(connect()) as db:
                migrate(db)
                occupied={r["time"] for r in db.execute(
                    "SELECT time FROM appointments WHERE appointment_type=? AND fid!=?",
                    (typ,self.fid)).fetchall()}
            with closing(connect()) as db:
                all_times=configured_slots(db)
            available=[t for t in all_times[self.page*24:(self.page+1)*24] if t not in occupied]
            if available:
                self.add_item(TimePicker(available))
            prev=discord.ui.Button(label="Earlier",style=discord.ButtonStyle.secondary,disabled=self.page==0)
            nextbtn=discord.ui.Button(label="Later",style=discord.ButtonStyle.secondary,disabled=self.page>=self.total_pages()-1)
            async def earlier(inter):
                self.page=max(0,self.page-1);self.render()
                await inter.response.edit_message(embed=self.embed(),view=self)
            async def later(inter):
                self.page=min(self.page+1,self.total_pages()-1);self.render()
                await inter.response.edit_message(embed=self.embed(),view=self)
            prev.callback=earlier;nextbtn.callback=later
            self.add_item(prev);self.add_item(nextbtn)
            if self.index==0 and len(self.types)>1:
                same=discord.ui.Button(
                    label="Same time for all: "+("ON" if self.same_for_all else "OFF"),
                    style=discord.ButtonStyle.primary if self.same_for_all else discord.ButtonStyle.secondary)
                async def toggle(inter):
                    self.same_for_all=not self.same_for_all
                    self.render()
                    await inter.response.edit_message(embed=self.embed(),view=self)
                same.callback=toggle
                self.add_item(same)
        else:
            confirm=discord.ui.Button(label="Confirm all appointments",style=discord.ButtonStyle.success)
            async def submit(inter):
                confirm.disabled=True
                try:
                    previous=await asyncio.to_thread(
                        book_many,self.user_id,self.guild_id,self.fid,dict(self.choices))
                except (ValueError,PermissionError,sqlite3.Error) as e:
                    self.render()
                    await inter.response.edit_message(content=str(e),embed=self.embed(),view=self)
                    return
                schedule=self.bot.get_cog("MinisterSchedule")
                menu=self.bot.get_cog("MinisterMenu")
                for typ,new_time in self.choices.items():
                    try:
                        if schedule:
                            await schedule.log_change(
                                action_type="reschedule" if previous.get(typ) else "add",
                                user=inter.user,appointment_type=typ,fid=self.fid,
                                old_time=previous.get(typ),new_time=new_time)
                        if menu:
                            await menu.update_channel_message(typ)
                    except Exception:
                        import logging
                        logging.getLogger(__name__).exception(
                            "Minister booking committed but schedule/log update failed")
                self.clear_items()
                with closing(connect()) as db:
                    dates=active_events(db)
                recap="\n".join(
                    f"• **{t}**: {dates.get(t,'date unknown')} at {time} UTC"
                    for t,time in self.choices.items())
                await inter.response.edit_message(
                    content="✅ **Reservations confirmed**\n"+recap+"\nYou will be tagged 5 minutes before and at each appointment.",
                    embed=None,view=self
                )
            confirm.callback=submit
            self.add_item(confirm)


class MinisterSelfService(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
        with closing(connect()) as db: migrate(db)
        self.reminders.start()

    async def cog_unload(self):
        self.reminders.cancel()

    @app_commands.command(name="minister",description="Book 1–3 minister appointments in one session")
    @app_commands.guild_only()
    async def minister(self,interaction:discord.Interaction):
        eligible=await asyncio.to_thread(permitted_fids,interaction.user.id,interaction.guild_id)
        if not eligible:
            await interaction.response.send_message(
                "No approved linked character. Use /register and ask your alliance admin to approve your player ID.",
                ephemeral=True
            )
            return
        if len(eligible)>1:
            view=discord.ui.View(timeout=180)
            select=discord.ui.Select(placeholder="Choose your character",options=[
                discord.SelectOption(label=(name or str(fid))[:80],description=str(fid),value=str(fid))
                for fid,name,_ in eligible[:25]
            ])
            async def choose(i):
                if i.user.id!=interaction.user.id: 
                    await i.response.send_message("Not your menu.",ephemeral=True);return
                wizard=BookingWizard(i.user.id,i.guild_id,int(select.values[0]),self.bot)
                await i.response.edit_message(content=None,embed=wizard.embed(),view=wizard)
            select.callback=choose
            view.add_item(select)
            await interaction.response.send_message("Choose the player ID to reserve for:",view=view,ephemeral=True)
        else:
            wizard=BookingWizard(interaction.user.id,interaction.guild_id,eligible[0][0],self.bot)
            await interaction.response.send_message(embed=wizard.embed(),view=wizard,ephemeral=True)

    @app_commands.command(name="minister_event_set",description="Admin: set a minister date and open/close its bookings")
    @app_commands.choices(appointment_type=[app_commands.Choice(name=t,value=t) for t in TYPES])
    @app_commands.guild_only()
    async def minister_event_set(self,interaction:discord.Interaction,appointment_type:str,date_utc:str,enabled:bool):
        if not PermissionManager.is_global_admin(interaction.user.id,interaction.guild_id):
            await interaction.response.send_message("Global admin access required.",ephemeral=True);return
        try:
            date=datetime.strptime(date_utc,"%Y-%m-%d").date().isoformat()
        except ValueError:
            await interaction.response.send_message("Enter the date as YYYY-MM-DD.",ephemeral=True);return
        with closing(connect()) as db:
            migrate(db)
            existing=db.execute("SELECT event_date FROM minister_event_dates WHERE appointment_type=?",
                                (appointment_type,)).fetchone()
            if existing and existing["event_date"]!=date:
                booked=db.execute("SELECT COUNT(*) FROM appointments WHERE appointment_type=?",
                                  (appointment_type,)).fetchone()[0]
                if booked:
                    await interaction.response.send_message(
                        "Cannot change this event date with existing bookings. Archive and clear the old cycle first.",
                        ephemeral=True
                    )
                    return
            db.execute("INSERT INTO minister_event_dates(appointment_type,event_date,enabled,updated_by) "
                       "VALUES(?,?,?,?) ON CONFLICT(appointment_type) DO UPDATE SET "
                       "event_date=excluded.event_date,enabled=excluded.enabled,updated_by=excluded.updated_by",
                       (appointment_type,date,int(enabled),interaction.user.id))
            db.commit()
        await interaction.response.send_message(
            f"{appointment_type}: {date} UTC — {'open' if enabled else 'closed'}. Existing bookings remain intact.",
            ephemeral=True
        )

    @app_commands.command(name="minister_reminder_channel",description="Global admin: set channel for personal minister mentions")
    @app_commands.guild_only()
    async def minister_reminder_channel(self,interaction:discord.Interaction,channel:discord.TextChannel):
        if not PermissionManager.is_global_admin(interaction.user.id,interaction.guild_id):
            await interaction.response.send_message("Global admin access required.",ephemeral=True);return
        me=interaction.guild.me
        perms=channel.permissions_for(me)
        if not perms.send_messages:
            await interaction.response.send_message("Bot must be able to send in this channel.",ephemeral=True);return
        with closing(connect()) as db:
            migrate(db)
            db.execute("INSERT INTO minister_reminder_channels(guild_id,channel_id) VALUES(?,?) "
                       "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id",
                       (interaction.guild_id,channel.id))
            db.commit()
        await interaction.response.send_message(
            f"Personal minister reminders will mention members in {channel.mention}. "
            "Make sure your members can see this channel.",
            ephemeral=True
        )

    @app_commands.command(name="minister_approve",description="Admin: approve a linked character for self-service minister booking")
    @app_commands.guild_only()
    async def minister_approve(self,interaction:discord.Interaction,player_id:int):
        with closing(sqlite3.connect(USERS_DB,timeout=30)) as db:
            row=db.execute("SELECT discord_id,discord_server_id,alliance FROM users WHERE fid=?",
                           (player_id,)).fetchone()
        if not row or row[0] is None or row[1]!=interaction.guild_id:
            await interaction.response.send_message("Player must be linked in this server first.",ephemeral=True);return
        if not PermissionManager.can_access_alliance(interaction.user.id,interaction.guild_id,row[2]):
            await interaction.response.send_message("You cannot approve characters in this alliance.",ephemeral=True);return
        with closing(connect()) as db:
            migrate(db)
            db.execute("INSERT INTO minister_player_approvals(fid,discord_id,guild_id,approved_by,approved_at) "
                       "VALUES(?,?,?,?,?) ON CONFLICT(fid) DO UPDATE SET discord_id=excluded.discord_id,"
                       "guild_id=excluded.guild_id,approved_by=excluded.approved_by,approved_at=excluded.approved_at",
                       (player_id,row[0],interaction.guild_id,interaction.user.id,datetime.now(UTC).isoformat()))
            db.commit()
        # Approved owners of already-booked legacy appointments are also entitled
        # to personal reminders, without changing or removing their saved slots.
        with closing(connect()) as db:
            rows=db.execute("SELECT appointment_type FROM appointments WHERE fid=?",
                            (player_id,)).fetchall()
        for record in rows:
            snapshot_owner(player_id,record["appointment_type"])
        await interaction.response.send_message(
            f"Approved linked player {player_id} for minister booking and personal reminders.",
            ephemeral=True)

    @tasks.loop(seconds=30)
    async def reminders(self):
        now=datetime.now(UTC)
        pending=[]
        with closing(connect()) as db:
            migrate(db)
            for typ,day in active_events(db).items():
                rows=db.execute(
                    "SELECT a.fid,a.time,o.discord_id FROM appointments a "
                    "JOIN minister_booking_owners o ON o.fid=a.fid AND o.appointment_type=a.appointment_type "
                    "WHERE a.appointment_type=?",(typ,)).fetchall()
                for r in rows:
                    start=datetime.fromisoformat(day+"T"+r["time"]+"+00:00")
                    for lead in (5,0):
                        due=start-timedelta(minutes=lead)
                        if not (due<=now<min(due+timedelta(minutes=2), start if lead else start+timedelta(minutes=2))):
                            continue
                        key=(typ,day,r["fid"],r["time"],lead)
                        if db.execute("SELECT 1 FROM minister_reminder_deliveries WHERE "
                                      "appointment_type=? AND event_date=? AND fid=? AND booking_time=? AND lead_minutes=?",
                                      key).fetchone():
                            continue
                        pending.append((key,r["discord_id"]))
        for (typ,day,fid,time,lead),user_id in pending:
            # Re-check exact booking and linked recipient before sending.
            with closing(connect()) as db:
                row=db.execute("SELECT 1 FROM appointments a JOIN minister_booking_owners o "
                               "ON o.fid=a.fid AND o.appointment_type=a.appointment_type "
                               "WHERE a.fid=? AND a.appointment_type=? AND a.time=? AND o.discord_id=?",
                               (fid,typ,time,user_id)).fetchone()
            if not row: continue
            schedule=self.bot.get_cog("MinisterSchedule")
            if not schedule: continue
            guild=await schedule.get_log_guild(None)
            if guild is None: continue
            with closing(connect()) as db:
                delivery=db.execute(
                    "SELECT channel_id FROM minister_reminder_channels WHERE guild_id=?",
                    (guild.id,)
                ).fetchone()
            if delivery is None: continue
            channel=guild.get_channel(delivery["channel_id"])
            if channel is None: continue
            message=(f"<@{user_id}> Your **{typ}** minister appointment "
                     +("starts in **5 minutes**!" if lead==5 else "starts **now**!")
                     +f"\nDate: {day} · Time: {time} UTC")
            try:
                await channel.send(message,allowed_mentions=discord.AllowedMentions(users=True,roles=False,everyone=False))
            except (discord.HTTPException,discord.Forbidden):
                continue
            with closing(connect()) as db:
                db.execute("INSERT OR IGNORE INTO minister_reminder_deliveries VALUES(?,?,?,?,?,?,?)",
                           (typ,day,fid,time,lead,user_id,datetime.now(UTC).isoformat()))
                db.commit()

    @reminders.before_loop
    async def ready(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(MinisterSelfService(bot))
