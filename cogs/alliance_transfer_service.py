"""Owner-only transfer of a managed alliance between guilds on the same WOSBOT.

Keep the alliance ID and member/history rows intact. Discord-local bindings must
be mapped to destination channels; old guild role grants never travel.
"""
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

A = "db/alliance.sqlite"
S = "db/settings.sqlite"
U = "db/users.sqlite"
I = "db/id_channel.sqlite"
M = "db/membership_tracking.sqlite"
KINDS = ("id", "log", "redeem", "sync", "troop")


def inspect(alliance_id):
    with closing(sqlite3.connect(A, timeout=30)) as db:
        row = db.execute("SELECT name,discord_server_id FROM alliance_list WHERE alliance_id=?",
                         (int(alliance_id),)).fetchone()
        if not row or row[1] is None:
            raise ValueError("Alliance does not have a registered source server")
        a_settings = db.execute(
            "SELECT channel_id, redemption_channel_id FROM alliancesettings WHERE alliance_id=?",
            (int(alliance_id),)).fetchone()
        try:
            troop = db.execute("SELECT channel_id FROM troop_quality_channels WHERE alliance_id=?",
                               (int(alliance_id),)).fetchone()
        except sqlite3.OperationalError:
            troop = None
    with closing(sqlite3.connect(S, timeout=30)) as db:
        log = db.execute("SELECT channel_id FROM alliance_logs WHERE alliance_id=?",
                         (int(alliance_id),)).fetchone()
        personals = db.execute("SELECT COUNT(*) FROM adminserver WHERE alliances_id=?",
                               (int(alliance_id),)).fetchone()[0]
        roles = db.execute("SELECT COUNT(*) FROM adminserver_role WHERE alliances_id=?",
                           (int(alliance_id),)).fetchone()[0]
    with closing(sqlite3.connect(I, timeout=30)) as db:
        ids = db.execute("SELECT channel_id FROM id_channels WHERE alliance_id=?",
                         (int(alliance_id),)).fetchone()
    with closing(sqlite3.connect(U, timeout=30)) as db:
        members = db.execute("SELECT COUNT(*) FROM users WHERE alliance=?",
                             (str(alliance_id),)).fetchone()[0]
    return {"id":int(alliance_id),"name":row[0],"source":int(row[1]),"members":members,
            "personal_admins":personals,"role_grants":roles,
            "channels":{"id":ids[0] if ids else None,
                        "log":log[0] if log else None,
                        "redeem":a_settings[1] if a_settings else None,
                        "sync":a_settings[0] if a_settings else None,
                        "troop":troop[0] if troop else None}}


def validate(plan, dest, bot):
    source = plan["source"]
    if source == dest.id:
        raise ValueError("Alliance already belongs to this Discord server")
    if not bot.get_guild(source):
        raise ValueError("Source server is not currently connected to this bot")
    bot_member = dest.me
    if bot_member is None:
        raise ValueError("Bot membership in destination server could not be verified")
    mappings = plan["mappings"]
    if set(mappings) != set(KINDS):
        raise ValueError("Complete all five channel mappings")
    seen = set()
    for kind, new_id in mappings.items():
        if new_id is None:
            if plan["channels"][kind] is not None:
                raise ValueError(f"Select a replacement for the existing {kind} channel")
            continue
        new_id = int(new_id)
        channel = dest.get_channel(new_id)
        if channel is None or not hasattr(channel, "permissions_for"):
            raise ValueError(f"{kind} channel must be a destination text channel")
        if not hasattr(channel, "send"):
            raise ValueError(f"{kind} channel cannot receive messages")
        perms = channel.permissions_for(bot_member)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            raise ValueError(f"Bot needs View, Send and Embed permissions in {kind} channel")
        if kind == "id" and not perms.read_message_history:
            raise ValueError("ID channel requires Read Message History permission")
        if new_id in seen:
            raise ValueError("Use a separate destination channel for each mapped purpose")
        seen.add(new_id)
    return True


def transfer(alliance_id, *, expected_source, destination, mappings, actor_id):
    """Execute after live Discord and Bot Owner checks, with source rechecked in DB.

    SQLite ATTACH transaction handles ordinary errors. Separate database backups
    are kept for recovery against process or host crashes.
    """
    aid, source, target = int(alliance_id), int(expected_source), int(destination)
    if source == target:
        raise ValueError("Source and destination must differ")
    data = inspect(aid)
    if data["source"] != source:
        raise ValueError("Alliance ownership changed during transfer; restart the wizard")
    if set(mappings) != set(KINDS):
        raise ValueError("Channel mapping is incomplete")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path("backups") / ("alliance-transfer-" + str(aid) + "-" + stamp)
    backup_dir.mkdir(parents=True, exist_ok=False)
    files = (A,S,U,I,M)
    # Backup all data BEFORE touching any ownership record. Keep backup on success.
    for src in files:
        if os.path.exists(src):
            with closing(sqlite3.connect(src, timeout=30)) as original, closing(
                    sqlite3.connect(str(backup_dir / Path(src).name))) as backup:
                original.backup(backup)
    with closing(sqlite3.connect(A, timeout=30)) as db:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("ATTACH DATABASE ? AS settings", (S,))
        db.execute("ATTACH DATABASE ? AS usersdb", (U,))
        db.execute("ATTACH DATABASE ? AS iddb", (I,))
        if os.path.exists(M):
            db.execute("ATTACH DATABASE ? AS tracking", (M,))
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute("SELECT discord_server_id FROM alliance_list WHERE alliance_id=?",
                             (aid,)).fetchone()
            if not row or row[0] != source:
                raise ValueError("Alliance ownership changed; no transfer performed")
            if db.execute("SELECT 1 FROM alliance_list WHERE alliance_id!=? "
                          "AND discord_server_id=? AND name=(SELECT name FROM alliance_list WHERE alliance_id=?)",
                          (aid,target,aid)).fetchone():
                raise ValueError("An alliance with this name already exists in destination")
            # Role/personal grants tied solely to this alliance must be revoked as
            # entities; simply removing the scope row can turn them into Server Admins.
            personal_ids = [r[0] for r in db.execute(
                "SELECT admin FROM settings.adminserver WHERE alliances_id=?", (aid,))]
            role_ids = [r[0] for r in db.execute(
                "SELECT role_id FROM settings.adminserver_role WHERE alliances_id=?", (aid,))]
            for admin in personal_ids:
                count = db.execute("SELECT COUNT(*) FROM settings.adminserver WHERE admin=?",
                                   (admin,)).fetchone()[0]
                if count == 1:
                    db.execute("DELETE FROM settings.admin WHERE id=? AND COALESCE(is_owner,0)=0 "
                               "AND COALESCE(is_initial,0)=0", (admin,))
            for role in role_ids:
                count = db.execute("SELECT COUNT(*) FROM settings.adminserver_role WHERE role_id=?",
                                   (role,)).fetchone()[0]
                if count == 1:
                    db.execute("DELETE FROM settings.admin_role WHERE role_id=? AND COALESCE(is_initial,0)=0",
                               (role,))
            db.execute("DELETE FROM settings.adminserver WHERE alliances_id=?", (aid,))
            db.execute("DELETE FROM settings.adminserver_role WHERE alliances_id=?", (aid,))
            db.execute("UPDATE alliance_list SET discord_server_id=? WHERE alliance_id=? AND discord_server_id=?",
                       (target,aid,source))
            db.execute("INSERT OR IGNORE INTO alliancesettings(alliance_id) VALUES (?)", (aid,))
            db.execute("UPDATE alliancesettings SET channel_id=?, redemption_channel_id=? WHERE alliance_id=?",
                       (mappings["sync"], mappings["redeem"],aid))
            db.execute("DELETE FROM settings.alliance_logs WHERE alliance_id=?", (aid,))
            if mappings["log"] is not None:
                db.execute("INSERT INTO settings.alliance_logs(alliance_id,channel_id) VALUES (?,?)",
                           (aid,mappings["log"]))
            db.execute("DELETE FROM iddb.id_channels WHERE alliance_id=?", (aid,))
            if mappings["id"] is not None:
                db.execute("INSERT INTO iddb.id_channels "
                           "(guild_id,alliance_id,channel_id,created_at,created_by) VALUES (?,?,?,?,?)",
                           (target,aid,mappings["id"],datetime.now(timezone.utc).isoformat(),actor_id))
            if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='troop_quality_channels'").fetchone():
                db.execute("DELETE FROM troop_quality_channels WHERE alliance_id=?", (aid,))
                if mappings["troop"] is not None:
                    db.execute("INSERT INTO troop_quality_channels "
                               "(alliance_id,guild_id,channel_id,updated_at,updated_by) VALUES (?,?,?,?,?)",
                               (aid,target,mappings["troop"],datetime.now(timezone.utc).isoformat(),actor_id))
            # Existing Discord identities remain linked to player IDs, but their
            # server binding is cleared until the user registers in destination.
            db.execute("UPDATE usersdb.users SET discord_server_id=NULL WHERE alliance=? "
                       "AND discord_server_id=?", (str(aid),source))
            if os.path.exists(M):
                # Only alliance-scoped temporary alliance rules follow the alliance.
                # Server-wide rules and tracking enablement remain server-owned.
                db.execute("UPDATE tracking.temporary_alliances SET guild_id=? "
                           "WHERE scope_alliance_id=? AND guild_id=?", (target,aid,source))
            db.commit()
        except Exception:
            db.rollback()
            raise
    return str(backup_dir)
