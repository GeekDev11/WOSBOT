"""Extended alliance export regression tests — isolated SQLite fixtures."""
import io
import json
import sqlite3

from openpyxl import load_workbook
from cogs.alliance_intel_export import build_export, to_xlsx, _safe_cell


def _db(path, statements):
    with sqlite3.connect(path) as db:
        db.executescript(statements)


def test_complete_export_scopes_all_history_and_preserves_files(tmp_path):
    _db(tmp_path / "users.sqlite", """
        CREATE TABLE users(fid INTEGER,nickname TEXT,furnace_lv INTEGER,kid INTEGER,
          power INTEGER,combat_power INTEGER,avatar_url TEXT,api_synced_at TEXT,alliance TEXT);
        INSERT INTO users VALUES (101,'A',80,1501,100,50,'url','2026-11-12T00:00:00','1');
        INSERT INTO users VALUES (102,'B',70,1501,90,45,NULL,NULL,'2');
    """)
    _db(tmp_path / "alliance.sqlite", """
        CREATE TABLE alliance_list(alliance_id INTEGER,name TEXT);
        INSERT INTO alliance_list VALUES(1,'VEN');
        INSERT INTO alliance_list VALUES(2,'OTHER');
    """)
    _db(tmp_path / "changes.sqlite", """
        CREATE TABLE nickname_changes(fid INTEGER,old_nickname TEXT,new_nickname TEXT,change_date TEXT,id INTEGER PRIMARY KEY);
        INSERT INTO nickname_changes VALUES(101,'Old','A','2026-11-12',1);
        INSERT INTO nickname_changes VALUES(102,'Other','B','2026-11-12',2);
        CREATE TABLE furnace_changes(fid INTEGER,old_furnace_lv INTEGER,new_furnace_lv INTEGER,change_date TEXT,id INTEGER PRIMARY KEY);
        CREATE TABLE power_changes(fid INTEGER,old_power INTEGER,new_power INTEGER,change_date TEXT,id INTEGER PRIMARY KEY);
        CREATE TABLE combat_power_changes(fid INTEGER,old_combat_power INTEGER,new_combat_power INTEGER,change_date TEXT,id INTEGER PRIMARY KEY);
    """)
    payload = json.dumps({"player":{"kid":1501,"x":77,"y":88,"kills":123},
                          "heroes":[{"id":41,"name":"Gregory","level":80,
                                     "gear":[{"plus":100,"mastery":20}]}]})
    with sqlite3.connect(tmp_path / "wos_player_observations.sqlite") as db:
        db.execute("CREATE TABLE observations (id INTEGER PRIMARY KEY, chief_id INTEGER, observed_at TEXT, source TEXT, payload_json TEXT)")
        db.execute("CREATE TABLE observation_changes (id INTEGER PRIMARY KEY, chief_id INTEGER, observed_at TEXT, path TEXT, old_json TEXT, new_json TEXT)")
        db.execute("INSERT INTO observations VALUES(1,101,'2026-11-12T00:00:00','whiteoutstats',?)",(payload,))
        db.execute("INSERT INTO observation_changes VALUES(1,101,'2026-11-12T00:00:00','player.x','76','77')")
    before = (tmp_path / "users.sqlite").read_bytes()
    sheets = build_export(1, db_dir=str(tmp_path), categories=["Complete Export"])
    assert len(sheets["Members"]) == 2
    assert sheets["Members"][1][0] == 101
    assert sheets["Members"][1][5:7] == [77,88]
    assert sheets["Name History"][1][0:3] == [101, "Old", "A"]
    assert sheets["State & Location"][1][1:4] == ["player.x", 76, 77]
    assert sheets["Heroes"][1][1] == 41
    assert sheets["Hero Equipment"][1][4:6] == [100,20]
    assert len(sheets["Observations"]) == 2
    assert (tmp_path / "users.sqlite").read_bytes() == before
    content = to_xlsx(sheets)
    book = load_workbook(io.BytesIO(content), read_only=True)
    assert "Hero Equipment" in book.sheetnames
    assert book["Members"]["A2"].value == 101
    book.close()


def test_filtered_export_never_includes_other_members(tmp_path):
    _db(tmp_path / "users.sqlite", """
        CREATE TABLE users(fid INTEGER,nickname TEXT,furnace_lv INTEGER,kid INTEGER,
          power INTEGER,combat_power INTEGER,avatar_url TEXT,api_synced_at TEXT,alliance TEXT);
        INSERT INTO users VALUES(101,'A',80,1501,100,50,NULL,NULL,'1');
        INSERT INTO users VALUES(102,'B',80,1501,100,50,NULL,NULL,'1');
    """)
    sheets = build_export(1, member_ids=[102], db_dir=str(tmp_path),
                          categories=["Current Player Data"])
    assert [r[0] for r in sheets["Members"][1:]] == [102]
    assert list(sheets) == ["Members", "Data Availability", "Export Metadata"]


def test_spreadsheet_formula_injection_is_escaped():
    assert _safe_cell("=HYPERLINK(\"x\")").startswith("'")
    assert _safe_cell(" @malicious").startswith("'")
    assert _safe_cell("Safe") == "Safe"


def test_member_xlsx_import_ignores_history_sheets():
    from cogs.alliance_member_operations import _member_import_text, _extract_ids_from_csv
    sheets = {
        'Members': [['ID', 'Name', 'FC Level', 'State'], [123456, 'Player', 80, 1501]],
        'Export Metadata': [['Property', 'Value'], ['Note', 'Do not restore history']],
    }
    raw = to_xlsx(sheets)
    csv_text = _member_import_text('export.xlsx', raw)
    assert _extract_ids_from_csv(csv_text) == ['123456']
    assert 'Do not restore history' not in csv_text


def test_older_provider_snapshots_are_exported(tmp_path):
    _db(tmp_path / 'users.sqlite', '''
        CREATE TABLE users(fid INTEGER,nickname TEXT,furnace_lv INTEGER,kid INTEGER,
          power INTEGER,combat_power INTEGER,avatar_url TEXT,api_synced_at TEXT,alliance TEXT);
        INSERT INTO users VALUES(501,'Five',80,1501,100,50,NULL,NULL,'1');
    ''')
    _db(tmp_path / 'whiteoutstats.sqlite', '''
        CREATE TABLE wos_player_history(chief_id INTEGER,source_cached_at REAL,
          observed_at TEXT,nickname TEXT,state_id INTEGER,furnace_level INTEGER,
          power INTEGER,kills INTEGER,vip_level INTEGER);
        INSERT INTO wos_player_history VALUES(501,1790000000,'2026-11-01','Five',1501,80,100,10,5);
    ''')
    sheets=build_export(1, db_dir=str(tmp_path), categories=['Player History'])
    assert 'Provider History' in sheets
    assert sheets['Provider History'][1][0] == 501
    assert sheets['Provider History'][1][6] == 100
