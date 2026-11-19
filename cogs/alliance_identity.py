"""Verified identity of a WOS managed home alliance.

The local alliance_id and home roster remain authoritative for administrative
ownership; game_aid + game_tag are verified API identity, never user input.
"""
import sqlite3

DB = "db/alliance.sqlite"


def migrate():
    with sqlite3.connect(DB, timeout=30) as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(alliance_list)")}
        if not columns:
            raise ValueError("Alliance database is not initialized.")
        if "game_aid" not in columns:
            db.execute("ALTER TABLE alliance_list ADD COLUMN game_aid INTEGER")
        if "game_tag" not in columns:
            db.execute("ALTER TABLE alliance_list ADD COLUMN game_tag TEXT COLLATE BINARY")
        # Keep repeated startup migrations idempotent.
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS managed_game_identity_unique "
                   "ON alliance_list(discord_server_id,kid,game_aid) WHERE game_aid IS NOT NULL")
        db.commit()


def verified_identity(alliance_id):
    try:
        with sqlite3.connect(DB,timeout=30) as db:
            row=db.execute(
                "SELECT discord_server_id,kid,game_aid,game_tag,name "
                "FROM alliance_list WHERE alliance_id=?", (alliance_id,)).fetchone()
        return row
    except sqlite3.OperationalError:
        return None


def save_verified(alliance_id, guild_id, resolved, expected_state=None):
    """Bind only an API-confirmed alliance; reject state mismatches and AID collisions."""
    migrate()
    with sqlite3.connect(DB,timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT discord_server_id,kid FROM alliance_list WHERE alliance_id=?",
                       (alliance_id,)).fetchone()
        if (row is None or row[0] is None or int(row[0])!=int(guild_id)
                or row[1] is None or int(row[1])!=int(resolved.state_id)):
            db.rollback()
            raise ValueError("Alliance state or server changed; verify again.")
        if expected_state is not None and int(row[1])!=int(expected_state):
            db.rollback()
            raise ValueError("Alliance home state changed before confirmation.")
        clash=db.execute("SELECT alliance_id FROM alliance_list "
                         "WHERE discord_server_id=? AND kid=? AND game_aid=? AND alliance_id!=?",
                         (guild_id,resolved.state_id,resolved.aid,alliance_id)).fetchone()
        if clash:
            db.rollback()
            raise ValueError("This game alliance is already linked to another managed alliance.")
        db.execute("UPDATE alliance_list SET game_aid=?,game_tag=? WHERE alliance_id=?",
                   (resolved.aid,resolved.tag,alliance_id))
        db.commit()


def create_verified(name,guild_id,resolved):
    """Atomic managed-alliance identity insert. Setup other tables separately."""
    migrate()
    with sqlite3.connect(DB,timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM alliance_list WHERE name=?", (name,)).fetchone():
            db.rollback()
            raise ValueError("An alliance with that display name already exists.")
        if db.execute("SELECT 1 FROM alliance_list WHERE discord_server_id=? AND kid=? AND game_aid=?",
                      (guild_id,resolved.state_id,resolved.aid)).fetchone():
            db.rollback()
            raise ValueError("That game alliance is already registered on this server.")
        cursor=db.execute(
            "INSERT INTO alliance_list(name,discord_server_id,kid,state_locked,game_aid,game_tag) "
            "VALUES (?,?,?,?,?,?)",
            (name,guild_id,resolved.state_id,0,resolved.aid,resolved.tag))
        alliance_id=cursor.lastrowid
        db.execute("INSERT INTO alliancesettings(alliance_id,channel_id,interval) VALUES (?,NULL,0)",
                   (alliance_id,))
        db.commit()
        return alliance_id
