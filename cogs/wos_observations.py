"""Full player observations and field-level change history for scheduled Player Sync.

Only persist sections actually returned by the configured API. Missing fields are
unknown, not a deletion or zero. Retain snapshots even when no change occurred.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = "db/wos_player_observations.sqlite"
SECTIONS = ("player", "heroes", "chief_gear", "alliance")


def _normalise(payload):
    if not isinstance(payload, dict):
        return {}
    return {section: payload[section] for section in SECTIONS if section in payload and payload[section] is not None}


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            output.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return output
    if isinstance(value, list):
        output = {}
        for i, item in enumerate(value):
            output.update(_flatten(item, f"{prefix}[{i}]"))
        return output
    return {prefix: value}


def save_observation(chief_id, payload, *, source="whiteoutstats", observed_at=None, path=DB_PATH):
    """Append every valid observation; record changes for values seen in both snapshots."""
    sections = _normalise(payload)
    if not sections:
        return 0
    observed_at = observed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with sqlite3.connect(path, timeout=30) as db:
        db.execute("PRAGMA busy_timeout = 30000")
        db.execute("""CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chief_id INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            source TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS observation_player_idx
            ON observations(chief_id, id DESC)""")
        db.execute("""CREATE TABLE IF NOT EXISTS observation_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chief_id INTEGER NOT NULL,
            observation_id INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            path TEXT NOT NULL,
            old_json TEXT NOT NULL,
            new_json TEXT NOT NULL
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS observation_changes_player_idx
            ON observation_changes(chief_id, observed_at DESC)""")
        old = db.execute("SELECT payload_json FROM observations WHERE chief_id=? ORDER BY id DESC LIMIT 1",
                         (int(chief_id),)).fetchone()
        previous = json.loads(old[0]) if old else {}
        # A response may omit an optional API section; keep its last known state.
        merged = dict(previous)
        merged.update(sections)
        cursor = db.execute(
            "INSERT INTO observations(chief_id, observed_at, source, payload_json) VALUES (?, ?, ?, ?)",
            (int(chief_id), observed_at, source, json.dumps(merged, ensure_ascii=False, sort_keys=True)))
        new_id = cursor.lastrowid
        old_fields, new_fields = _flatten(previous), _flatten(merged)
        changes = [
            (int(chief_id), new_id, observed_at, field,
             json.dumps(old_fields[field], ensure_ascii=False),
             json.dumps(value, ensure_ascii=False))
            for field, value in new_fields.items()
            if field in old_fields and old_fields[field] != value
        ]
        db.executemany(
            "INSERT INTO observation_changes(chief_id, observation_id, observed_at, path, old_json, new_json) "
            "VALUES (?, ?, ?, ?, ?, ?)", changes)
        return len(changes)


def fetch_changes(chief_id, *, limit=100, path=DB_PATH):
    if not os.path.isfile(path):
        return []
    with sqlite3.connect(path) as db:
        return db.execute(
            "SELECT observed_at, path, old_json, new_json FROM observation_changes "
            "WHERE chief_id=? ORDER BY id DESC LIMIT ?", (int(chief_id), int(limit))).fetchall()
