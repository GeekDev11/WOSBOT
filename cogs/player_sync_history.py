"""Durable Player Sync change audit in the same SQLite transaction as player edits.

Only changes caused by Player Sync are captured. Existing historic counters
cannot be reconstructed from them. History is not affected by Discord log TTL.
"""
import csv
import io
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

DB = "db/users.sqlite"
ALLIANCE_DB = "db/alliance.sqlite"


def migrate(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS player_sync_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        alliance_id INTEGER NOT NULL,
        guild_id INTEGER,
        fid INTEGER NOT NULL,
        player_name TEXT NOT NULL,
        field TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS player_sync_changes_alliance "
                 "ON player_sync_changes(alliance_id,id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS player_sync_changes_run "
                 "ON player_sync_changes(run_id,id DESC)")


def guild_of(alliance_id):
    try:
        with closing(sqlite3.connect(ALLIANCE_DB, timeout=30)) as conn:
            row = conn.execute("SELECT discord_server_id FROM alliance_list WHERE alliance_id=?",
                               (int(alliance_id),)).fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except sqlite3.OperationalError:
        # Older installations and minimal test databases may lack this column.
        # The change still belongs to its alliance; guild is optional audit metadata.
        return None


def append(conn, *, run_id, alliance_id, fid, player_name, changes, guild_id):
    """Call before commit on the SAME users.sqlite transaction as player changes."""
    if not changes:
        return
    migrate(conn)
    recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO player_sync_changes "
        "(run_id,recorded_at,alliance_id,guild_id,fid,player_name,field,old_value,new_value) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [(run_id,recorded_at,int(alliance_id),guild_id,int(fid),str(player_name)[:100],
          str(field),None if old is None else str(old),None if new is None else str(new))
         for field, old, new in changes])


def list_changes(alliance_ids, *, page=0, page_size=10):
    ids = sorted(set(int(a) for a in alliance_ids))
    if not ids:
        return 0, []
    params = tuple(ids)
    where = "alliance_id IN (" + ",".join("?" for _ in ids) + ")"
    with closing(sqlite3.connect(DB, timeout=30)) as conn:
        migrate(conn)
        count = conn.execute("SELECT COUNT(*) FROM player_sync_changes WHERE "+where, params).fetchone()[0]
        rows = conn.execute(
            "SELECT recorded_at,alliance_id,fid,player_name,field,old_value,new_value,run_id "
            "FROM player_sync_changes WHERE "+where+" ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, int(page_size), int(page)*int(page_size))).fetchall()
    return count, rows


def csv_bytes(alliance_ids, *, maximum=50000):
    ids = sorted(set(int(a) for a in alliance_ids))
    if not ids:
        return b"", 0, False
    with closing(sqlite3.connect(DB, timeout=30)) as conn:
        migrate(conn)
        where = "alliance_id IN (" + ",".join("?" for _ in ids) + ")"
        count = conn.execute("SELECT COUNT(*) FROM player_sync_changes WHERE "+where, ids).fetchone()[0]
        rows = conn.execute(
            "SELECT recorded_at,alliance_id,fid,player_name,field,old_value,new_value,run_id "
            "FROM player_sync_changes WHERE "+where+" ORDER BY id DESC LIMIT ?",
            (*ids, maximum)).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(("timestamp_utc","alliance_id","player_id","player_name","field",
                     "old_value","new_value","run_id"))
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig"), len(rows), count > len(rows)
