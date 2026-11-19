"""Membership tracking: conservative observations and state-scoped temporary destinations.

This service never changes users.alliance or deletes member rows. Its database is
separate so an API observation cannot mutate the authoritative home roster.
"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

DB = "db/membership_tracking.sqlite"
ALLIANCE_DB = "db/alliance.sqlite"
USERS_DB = "db/users.sqlite"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    return db


def initialize():
    with closing(connect()) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS temporary_alliances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                state_id INTEGER NOT NULL,
                game_alliance_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                scope_alliance_id INTEGER,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(guild_id, state_id, game_alliance_id, scope_alliance_id)
            );
            CREATE TABLE IF NOT EXISTS observations (
                fid INTEGER PRIMARY KEY,
                home_alliance INTEGER NOT NULL,
                state_id INTEGER,
                game_alliance_id INTEGER,
                game_tag TEXT,
                observed_at TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS movement_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fid INTEGER NOT NULL,
                home_alliance INTEGER NOT NULL,
                from_state INTEGER,
                to_state INTEGER,
                game_alliance_id INTEGER,
                game_tag TEXT,
                category TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                observed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                decision TEXT,
                decided_by INTEGER,
                decided_at TEXT,
                version INTEGER NOT NULL DEFAULT 1
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_pending_case
                ON movement_cases(fid, home_alliance) WHERE status='pending';
            CREATE TABLE IF NOT EXISTS visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fid INTEGER NOT NULL,
                home_alliance INTEGER NOT NULL,
                state_id INTEGER,
                game_alliance_id INTEGER,
                game_tag TEXT,
                started_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                ended_at TEXT,
                origin TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_visit
                ON visits(fid, home_alliance) WHERE ended_at IS NULL;
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL,
                fid INTEGER NOT NULL,
                home_alliance INTEGER NOT NULL,
                actor_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            );
        """)
        db.commit()


def is_enabled(guild_id):
    if guild_id is None:
        return False
    initialize()
    with closing(connect()) as db:
        row = db.execute("SELECT enabled FROM settings WHERE guild_id=?", (guild_id,)).fetchone()
        return bool(row and row["enabled"])


def set_enabled(guild_id, enabled):
    initialize()
    with closing(connect()) as db:
        db.execute("INSERT INTO settings(guild_id, enabled) VALUES (?, ?) "
                   "ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled",
                   (guild_id, int(bool(enabled))))
        db.commit()


def home_info(alliance_id):
    with sqlite3.connect(ALLIANCE_DB) as db:
        return db.execute("SELECT discord_server_id, kid, name FROM alliance_list WHERE alliance_id=?",
                          (alliance_id,)).fetchone()


def member_home(fid):
    with sqlite3.connect(USERS_DB) as db:
        row = db.execute("SELECT alliance FROM users WHERE fid=?", (fid,)).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def add_temporary(guild_id, state_id, aid, tag, actor_id, scope_alliance_id=None):
    initialize()
    guild_id, state_id, aid = int(guild_id), int(state_id), int(aid)
    if min(guild_id, state_id, aid) <= 0 or not str(tag).strip():
        raise ValueError("Valid state, game alliance ID and tag are required")
    with sqlite3.connect(ALLIANCE_DB) as adb:
        managed = adb.execute("SELECT 1 FROM alliance_list WHERE discord_server_id=? AND kid=? LIMIT 1",
                              (guild_id, state_id)).fetchone()
    if not managed:
        raise ValueError("This server does not manage an alliance in the selected state")
    if scope_alliance_id is not None:
        home = home_info(int(scope_alliance_id))
        if not home or int(home[0]) != guild_id or int(home[1]) != state_id:
            raise ValueError("Scope alliance must belong to the selected server and state")
    with closing(connect()) as db:
        db.execute("DELETE FROM temporary_alliances WHERE guild_id=? AND state_id=? "
                   "AND game_alliance_id=? AND scope_alliance_id IS ?",
                   (guild_id, state_id, aid, scope_alliance_id))
        db.execute("INSERT INTO temporary_alliances "
                   "(guild_id,state_id,game_alliance_id,tag,scope_alliance_id,created_by,created_at) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (guild_id, state_id, aid, str(tag).strip()[:20], scope_alliance_id, actor_id, now()))
        db.commit()


def temporary_list(guild_id, allowed_alliances, is_server=False):
    initialize()
    with closing(connect()) as db:
        rows = db.execute("SELECT * FROM temporary_alliances WHERE guild_id=? AND enabled=1 "
                          "ORDER BY state_id,tag", (guild_id,)).fetchall()
    allowed = set(map(int, allowed_alliances))
    return [dict(r) for r in rows if (is_server or r["scope_alliance_id"] is None
                                      or r["scope_alliance_id"] in allowed)]


def remove_temporary(rule_id, guild_id, allowed_alliances, is_server=False):
    initialize()
    with closing(connect()) as db:
        row = db.execute("SELECT scope_alliance_id FROM temporary_alliances "
                         "WHERE id=? AND guild_id=?", (rule_id, guild_id)).fetchone()
        if row is None or (not is_server and
                           (row["scope_alliance_id"] is None or
                            row["scope_alliance_id"] not in set(map(int, allowed_alliances)))):
            return False
        db.execute("UPDATE temporary_alliances SET enabled=0 WHERE id=?", (rule_id,))
        db.commit()
        return True


def _temporary(db, guild, state, aid, home):
    return bool(db.execute("SELECT 1 FROM temporary_alliances "
                           "WHERE guild_id=? AND state_id=? AND game_alliance_id=? AND enabled=1 "
                           "AND (scope_alliance_id IS NULL OR scope_alliance_id=?) LIMIT 1",
                           (guild,state,aid,home)).fetchone())


def _close_visit(db, fid, home, stamp):
    db.execute("UPDATE visits SET ended_at=?,last_seen_at=? "
               "WHERE fid=? AND home_alliance=? AND ended_at IS NULL",
               (stamp,stamp,fid,home))


def _open_visit(db, fid, home, state, aid, tag, stamp, origin):
    old=db.execute("SELECT id,state_id,game_alliance_id FROM visits WHERE fid=? AND home_alliance=? "
                   "AND ended_at IS NULL", (fid,home)).fetchone()
    if old and (old["state_id"], old["game_alliance_id"]) == (state,aid):
        db.execute("UPDATE visits SET last_seen_at=? WHERE id=?", (stamp,old["id"]))
    else:
        _close_visit(db,fid,home,stamp)
        db.execute("INSERT INTO visits(fid,home_alliance,state_id,game_alliance_id,game_tag,"
                   "started_at,last_seen_at,origin) VALUES (?,?,?,?,?,?,?,?)",
                   (fid,home,state,aid,tag,stamp,stamp,origin))


def _close_case(db, fid, home, reason, stamp):
    db.execute("UPDATE movement_cases SET status='resolved',decision=?,updated_at=?, "
               "decided_at=?,version=version+1 WHERE fid=? AND home_alliance=? AND status='pending'",
               (reason,stamp,stamp,fid,home))


def observe(fid, home_alliance, payload, observed_at=None):
    """Consume successful API data; uncertain/missing alliance identity is not a departure."""
    initialize()
    home = home_info(home_alliance)
    if not home or home[0] is None or home[1] is None or not is_enabled(home[0]):
        return "disabled"
    if member_home(fid) != int(home_alliance):
        return "membership_changed"
    player = payload.get("player", payload) if isinstance(payload,dict) else None
    if not isinstance(player,dict):
        return "unknown"
    data = player.get("alliance")
    if not isinstance(data,dict):
        return "unknown"
    aid = data.get("aid")
    state = player.get("kid", player.get("state"))
    if not isinstance(aid,(int,str)) or not str(aid).isdigit():
        return "unknown"
    if not isinstance(state,(int,str)) or not str(state).isdigit():
        return "unknown"
    aid,state,guild,home_state = int(aid),int(state),int(home[0]),int(home[1])
    if aid <= 0 or state <= 0:
        return "unknown"
    tag=str(data.get("abbr") or data.get("tag") or "")[:20]
    stamp=observed_at or now()
    with closing(connect()) as db:
        old=db.execute("SELECT * FROM observations WHERE fid=?", (fid,)).fetchone()
        if old and old["observed_at"] >= stamp:
            return "stale"
        if old and old["home_alliance"] != home_alliance:
            old=None
        # Existing users can be visiting when tracking is first enabled.
        # The first observation establishes the baseline; never produces a case.
        if not old:
            status="baseline"
        elif state != home_state:
            status="state_transfer"
        elif _temporary(db,guild,state,aid,home_alliance):
            status="temporary"
        elif db.execute("SELECT 1 FROM visits WHERE fid=? AND home_alliance=? AND "
                        "state_id=? AND game_alliance_id=? AND ended_at IS NULL "
                        "AND origin='individual' LIMIT 1",
                        (fid,home_alliance,state,aid)).fetchone():
            status="temporary"
        elif old["state_id"]==state and old["game_alliance_id"]==aid:
            if old["status"] == "temporary":
                status="review"  # revoked state-wide temporary approval
            elif old["status"] in ("review","state_transfer"):
                resolved=db.execute("SELECT decision FROM movement_cases WHERE fid=? AND "
                                    "home_alliance=? AND status='resolved' AND "
                                    "game_alliance_id=? AND to_state=? ORDER BY id DESC LIMIT 1",
                                    (fid,home_alliance,aid,state)).fetchone()
                status="kept" if resolved and resolved["decision"]=="keep" else old["status"]
            else:
                status=old["status"]
        elif tag and tag.casefold()==str(home[2]).casefold():
            status="home"
        else:
            status="review"
        db.execute("INSERT INTO observations(fid,home_alliance,state_id,game_alliance_id,"
                   "game_tag,observed_at,first_seen_at,status) VALUES (?,?,?,?,?,?,?,?) "
                   "ON CONFLICT(fid) DO UPDATE SET home_alliance=excluded.home_alliance,"
                   "state_id=excluded.state_id,game_alliance_id=excluded.game_alliance_id,"
                   "game_tag=excluded.game_tag,observed_at=excluded.observed_at,status=excluded.status",
                   (fid,home_alliance,state,aid,tag,stamp,stamp if not old else old["first_seen_at"],status))
        if status=="temporary":
            _open_visit(db,fid,home_alliance,state,aid,tag,stamp,"registry")
            _close_case(db,fid,home_alliance,"approved_temporary",stamp)
        elif status=="home":
            _close_visit(db,fid,home_alliance,stamp)
            _close_case(db,fid,home_alliance,"returned",stamp)
        elif status in ("review","state_transfer"):
            current=db.execute("SELECT id FROM movement_cases WHERE fid=? AND home_alliance=? "
                               "AND status='pending'", (fid,home_alliance)).fetchone()
            if current:
                db.execute("UPDATE movement_cases SET to_state=?,game_alliance_id=?,"
                           "game_tag=?,category=?,updated_at=?,version=version+1 WHERE id=?",
                           (state,aid,tag,status,stamp,current["id"]))
            else:
                db.execute("INSERT INTO movement_cases(fid,home_alliance,from_state,to_state,"
                           "game_alliance_id,game_tag,category,observed_at,updated_at) "
                           "VALUES (?,?,?,?,?,?,?,?,?)",
                           (fid,home_alliance,home_state,state,aid,tag,status,stamp,stamp))
        db.commit()
    return status


def dashboard(guild_id, allowed_alliances):
    initialize()
    ids=list(map(int,allowed_alliances))
    if not ids:
        return {"active":0,"visiting":0,"pending":0,"transfers":0}
    placeholders=",".join("?" for _ in ids)
    with sqlite3.connect(USERS_DB) as db:
        active=db.execute("SELECT COUNT(*) FROM users WHERE alliance IN ("+placeholders+")",ids).fetchone()[0]
    with closing(connect()) as db:
        visiting=db.execute("SELECT COUNT(*) FROM visits WHERE ended_at IS NULL AND home_alliance IN ("+placeholders+")",ids).fetchone()[0]
        pending=db.execute("SELECT COUNT(*) FROM movement_cases WHERE status='pending' AND home_alliance IN ("+placeholders+")",ids).fetchone()[0]
        transfers=db.execute("SELECT COUNT(*) FROM movement_cases WHERE status='pending' AND category='state_transfer' "
                             "AND home_alliance IN ("+placeholders+")",ids).fetchone()[0]
    return dict(active=active,visiting=visiting,pending=pending,transfers=transfers)


def list_cases(allowed_alliances, status="pending", limit=25):
    initialize()
    ids=list(map(int,allowed_alliances))
    if not ids:
        return []
    ph=",".join("?" for _ in ids)
    with closing(connect()) as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM movement_cases WHERE home_alliance IN ("+ph+") AND status=? "
            "ORDER BY updated_at DESC LIMIT ?", (*ids,status,limit)).fetchall()]


def list_visits(allowed_alliances, limit=25):
    initialize()
    ids=list(map(int,allowed_alliances))
    if not ids:
        return []
    ph=",".join("?" for _ in ids)
    with closing(connect()) as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM visits WHERE home_alliance IN ("+ph+") AND ended_at IS NULL "
            "ORDER BY last_seen_at DESC LIMIT ?", (*ids,limit)).fetchall()]


def decide(case_id, version, actor_id, allowed_alliances, action, detail=""):
    """Safe first release: no destructive or home-roster updates."""
    if action not in ("keep","temporary"):
        raise ValueError("Only non-destructive decisions are enabled during safe rollout")
    initialize()
    with closing(connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT * FROM movement_cases WHERE id=? AND status='pending' AND version=?",
                       (case_id,version)).fetchone()
        if not row or int(row["home_alliance"]) not in set(map(int,allowed_alliances)):
            db.rollback()
            return False
        if action=="temporary" and (row["category"]=="state_transfer" or not row["game_alliance_id"]):
            db.rollback()
            return False
        stamp=now()
        if action=="temporary" and row["category"]!="state_transfer" and row["game_alliance_id"]:
            _open_visit(db,row["fid"],row["home_alliance"],row["to_state"],
                        row["game_alliance_id"],row["game_tag"],stamp,"individual")
        db.execute("UPDATE movement_cases SET status='resolved',decision=?,decided_by=?,"
                   "decided_at=?,updated_at=?,version=version+1 WHERE id=?",
                   (action,actor_id,stamp,stamp,case_id))
        db.execute("INSERT INTO decisions(case_id,fid,home_alliance,actor_id,action,detail,created_at) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (case_id,row["fid"],row["home_alliance"],actor_id,action,detail[:200],stamp))
        db.commit()
        return True
