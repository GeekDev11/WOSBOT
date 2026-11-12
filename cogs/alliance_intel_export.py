"""Read-only alliance intelligence export from WOSBOT's existing SQLite data."""
import io
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

DB_DIR = "db"
CATEGORIES = ("Current Player Data", "Player History", "Heroes & Equipment",
              "Equipment History", "Complete Export")
SHEET_HEADERS = {
    "Members": ["ID", "Alliance", "Name", "FC Level", "State", "X", "Y",
                "Power", "Combat Power", "Kills", "VIP", "Avatar", "Last Synced",
                "Source Observation", "Source", "Data Availability"],
    "Name History": ["ID", "Old Name", "New Name", "Recorded At", "Source"],
    "FC History": ["ID", "Old FC", "New FC", "Recorded At", "Source"],
    "Power History": ["ID", "Metric", "Old Value", "New Value", "Recorded At", "Source"],
    "State & Location": ["ID", "Field", "Old Value", "New Value", "Observed At", "Source"],
    "Alliance History": ["ID", "Alliance ID", "Event", "Old Value", "New Value", "Observed At", "Source"],
    "Heroes": ["ID", "Hero Identifier", "Name", "Level", "Stars", "Skills", "Exclusive Weapon",
               "Last Observed", "Availability", "Raw Hero JSON"],
    "Hero Equipment": ["ID", "Hero Identifier", "Slot", "Gear", "Enhancement",
                       "Mastery", "Last Observed", "Raw Gear JSON"],
    "Chief Equipment": ["ID", "Slot", "Gear", "Level", "Last Observed",
                        "Availability", "Raw Gear JSON"],
    "Equipment History": ["ID", "Field", "Old Value", "New Value", "Observed At", "Source"],
    "Observations": ["ID", "Observed At", "Source", "Captured Data"],
    "Data Availability": ["ID", "Field", "Availability", "Last Observation"],
    "Export Metadata": ["Property", "Value"],
}


def _read(path, statement, params=()):
    if not os.path.isfile(path):
        return []
    try:
        with closing(sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)) as db:
            return db.execute(statement, params).fetchall()
    except sqlite3.Error:
        return []


def _json(value):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return None


def _json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else ""


def _get(obj, *keys):
    for key in keys:
        if isinstance(obj, dict) and obj.get(key) is not None:
            return obj[key]
    return None


def _safe_cell(value):
    # A leading formula marker remains untrusted even in data provided by an API.
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = _json_text(value)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _add(sheets, name, *values):
    sheets[name].append([_safe_cell(v) for v in values])


def _ensure():
    return {name: [list(headers)] for name, headers in SHEET_HEADERS.items()}


def build_export(alliance_id, *, member_ids=None, db_dir=DB_DIR, categories=None):
    """Return worksheet rows. No writes, no API calls, and no historical mutations.

    Always scope history to the currently selected member IDs (not all database rows).
    """
    categories = set(categories or CATEGORIES)
    if "Complete Export" in categories:
        categories = set(CATEGORIES)
    sheets = _ensure()
    users_db = os.path.join(db_dir, "users.sqlite")
    query = ("SELECT fid,nickname,furnace_lv,kid,power,combat_power,avatar_url,"
             "api_synced_at,alliance FROM users")
    params = ()
    if alliance_id != "all":
        query += " WHERE alliance=?"
        params = (str(alliance_id),)
    else:
        query += " WHERE alliance IS NOT NULL AND alliance != ''"
    members = _read(users_db, query, params)
    if member_ids is not None:
        allowed = set(int(x) for x in member_ids)
        members = [m for m in members if int(m[0]) in allowed]
    fids = {int(m[0]) for m in members}
    names = dict(_read(os.path.join(db_dir, "alliance.sqlite"),
                       "SELECT alliance_id,name FROM alliance_list"))
    if alliance_id == 'all':
        allowed = {str(aid) for aid in names}
        members = [m for m in members if str(m[8]) in allowed]
        fids = {int(m[0]) for m in members}
    changes_db = os.path.join(db_dir, "changes.sqlite")
    observation_db = os.path.join(db_dir, "wos_player_observations.sqlite")
    latest = {}
    if fids:
        # Indexed by chief_id in the existing observation table.
        for fid in fids:
            rows = _read(observation_db,
                         "SELECT observed_at,source,payload_json FROM observations "
                         "WHERE chief_id=? ORDER BY id DESC LIMIT 1", (fid,))
            if rows:
                latest[fid] = (rows[0][0], rows[0][1], _json(rows[0][2]) or {})

    for fid, name, fc, state, power, cp, avatar, synced, aid in members:
        fid = int(fid)
        recorded_at, source, payload = latest.get(fid, (None, None, {}))
        p = payload.get("player", {}) if isinstance(payload, dict) else {}
        if not isinstance(p, dict):
            p = {}
        _add(sheets, "Members", fid, names.get(int(aid), str(aid)) if aid else "",
             name, fc, _get(p, "kid") if _get(p, "kid") is not None else state,
             _get(p, "x"), _get(p, "y"),
             _get(p, "power") if _get(p, "power") is not None else power,
             cp, _get(p, "kills"), _get(p, "vip"), avatar, synced,
             recorded_at, source, "Last saved observation (not necessarily live)" if recorded_at else "Local member record only")
        for field, value in (('State', _get(p, 'kid')), ('X', _get(p, 'x')),
                             ('Y', _get(p, 'y')), ('Kills', _get(p, 'kills')),
                             ('VIP', _get(p, 'vip')), ('Heroes', payload.get('heroes')),
                             ('Chief Gear', payload.get('chief_gear'))):
            if field == 'Chief Gear' and isinstance(value, dict) and value.get('hidden') is True:
                availability = 'Hidden by source'
            elif value is None:
                availability = 'Not reported'
            elif isinstance(value, (dict, list)) and not value:
                availability = 'Empty response (not proof of none owned)'
            else:
                availability = 'Last recorded API value; freshness unverified'
            _add(sheets, 'Data Availability', fid, field, availability, recorded_at)
        if not ({"Heroes & Equipment", "Equipment History"} & categories):
            continue
        heroes = payload.get("heroes") if isinstance(payload, dict) else None
        if isinstance(heroes, dict):
            heroes = heroes.get("items") or heroes.get("heroes")
        if isinstance(heroes, list):
            for index, hero in enumerate(heroes):
                if not isinstance(hero, dict):
                    continue
                hid = _get(hero, "id", "hero_id", "heroId") or f"lineup-position-{index+1}"
                _add(sheets, "Heroes", fid, hid, _get(hero, "name"), _get(hero, "level"),
                     _get(hero, "stars", "star"), _get(hero, "skills", "skill_levels"),
                     _get(hero, "widget", "exclusive_weapon"), recorded_at,
                     "Reported Arena heroes, not full hero inventory", hero)
                gear = _get(hero, "gear", "equipment", "gears")
                if isinstance(gear, list):
                    for slot, item in enumerate(gear, 1):
                        if not isinstance(item, dict):
                            continue
                        _add(sheets, "Hero Equipment", fid, hid,
                             _get(item, "slot") or slot, _get(item, "name", "icon"),
                             _get(item, "plus", "level"), _get(item, "mastery"), recorded_at, item)
        chief = payload.get("chief_gear") if isinstance(payload, dict) else None
        if isinstance(chief, dict):
            items = chief.get("items")
            availability = "Hidden by provider" if chief.get("hidden") is True else "Reported"
            if isinstance(items, list):
                for index, item in enumerate(items, 1):
                    if isinstance(item, dict):
                        _add(sheets, "Chief Equipment", fid, _get(item, "slot") or index,
                             _get(item, "name", "icon"), _get(item, "level", "plus"),
                             recorded_at, availability, item)
            elif chief.get("hidden") is True:
                _add(sheets, "Chief Equipment", fid, "", "", "", recorded_at, availability, "")

    if "Player History" in categories or "Equipment History" in categories:
        for fid in fids:
            for table, sheet, oldcol, newcol in (
                ("nickname_changes", "Name History", "old_nickname", "new_nickname"),
                ("furnace_changes", "FC History", "old_furnace_lv", "new_furnace_lv"),
            ):
                for old, new, when in _read(changes_db,
                    f"SELECT {oldcol},{newcol},change_date FROM {table} WHERE fid=? ORDER BY id", (fid,)):
                    _add(sheets, sheet, fid, old, new, when, "Local member edit / sync")
            for table, metric, oldcol, newcol in (
                ("power_changes", "Power", "old_power", "new_power"),
                ("combat_power_changes", "Combat Power", "old_combat_power", "new_combat_power"),
            ):
                for old, new, when in _read(changes_db,
                    f"SELECT {oldcol},{newcol},change_date FROM {table} WHERE fid=? ORDER BY id", (fid,)):
                    _add(sheets, "Power History", fid, metric, old, new, when, "Local member edit / sync")
            for when, field, old, new in _read(observation_db,
                "SELECT observed_at,path,old_json,new_json FROM observation_changes "
                "WHERE chief_id=? ORDER BY id", (fid,)):
                if field.startswith(("player.kid", "player.x", "player.y")):
                    _add(sheets, "State & Location", fid, field, _json(old), _json(new),
                         when, "WhiteoutStats observation")
                elif field.startswith(("heroes", "chief_gear")):
                    # Raw comparison paths are preserved: position changes must not be
                    # presented as verified upgrades without stable entity IDs.
                    _add(sheets, "Equipment History", fid, field, _json(old), _json(new),
                         when, "Observed JSON change (upgrade not verified)")
            for aid, event, old, new, when in _read(
                os.path.join(db_dir, "whiteoutstats.sqlite"),
                "SELECT alliance_id,event_type,old_value,new_value,observed_at "
                "FROM wos_alliance_events WHERE chief_id=? ORDER BY id", (fid,)):
                _add(sheets, "Alliance History", fid, aid, event, old, new, when,
                     "Observed roster change; not proof of state transfer")
    if "Complete Export" in categories:
        for fid in fids:
            for when, src, data in _read(observation_db,
                "SELECT observed_at,source,payload_json FROM observations WHERE chief_id=? ORDER BY id", (fid,)):
                _add(sheets, "Observations", fid, when, src, data)

    _add(sheets, "Export Metadata", "Generated UTC", datetime.now(timezone.utc).isoformat())
    _add(sheets, "Export Metadata", "Alliance scope", alliance_id)
    _add(sheets, "Export Metadata", "Selected members", len(fids))
    _add(sheets, "Export Metadata", "API requests during export", 0)
    for filename in ("users.sqlite", "changes.sqlite", "whiteoutstats.sqlite", "wos_player_observations.sqlite"):
        _add(sheets, "Export Metadata", "Source: " + filename,
             "Present" if os.path.isfile(os.path.join(db_dir, filename)) else "Unavailable")
    _add(sheets, "Export Metadata", "History meaning", "Detected between observations; times are not precise in-game event times")
    _add(sheets, "Export Metadata", "Missing data", "Blank = not reported; no value is inferred")
    _add(sheets, "Export Metadata", "Hero coverage", "Arena lineup only when provided by API")
    _add(sheets, "Export Metadata", "Freshness", "Observations may contain retained values from earlier API responses")
    keep = {"Members", "Data Availability", "Export Metadata"}
    if "Player History" in categories:
        keep.update(("Name History", "FC History", "Power History", "State & Location", "Alliance History"))
    if "Heroes & Equipment" in categories:
        keep.update(("Heroes", "Hero Equipment", "Chief Equipment"))
    if "Equipment History" in categories:
        keep.add("Equipment History")
    if "Complete Export" in categories:
        keep.add("Observations")
    return {k: sheets[k] for k in SHEET_HEADERS if k in keep}


def to_xlsx(sheets):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append([_safe_cell(v) for v in row])
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="25324A")
        for i, column in enumerate(ws.columns, 1):
            max_width = max((len(str(c.value or "")) for c in list(column)[:150]), default=12)
            ws.column_dimensions[get_column_letter(i)].width = min(max(12, max_width + 2), 50)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
