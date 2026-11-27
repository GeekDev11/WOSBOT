"""Regression coverage for cross-guild alliance transfer data safety."""
import sqlite3
from pathlib import Path

import pytest
from cogs import alliance_transfer_service as svc


def _db(path, script):
    with sqlite3.connect(str(path)) as c:
        c.executescript(script)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    folder=tmp_path/"db"
    folder.mkdir()
    a=folder/"alliance.sqlite"
    s=folder/"settings.sqlite"
    u=folder/"users.sqlite"
    i=folder/"id_channel.sqlite"
    m=folder/"membership_tracking.sqlite"
    g=folder/"giftcode.sqlite"
    _db(a, """
    CREATE TABLE alliance_list(alliance_id INTEGER PRIMARY KEY,name TEXT,
        discord_server_id INTEGER,kid INTEGER);
    CREATE TABLE alliancesettings(alliance_id INTEGER PRIMARY KEY,channel_id INTEGER,
        redemption_channel_id INTEGER);
    CREATE TABLE troop_quality_channels(alliance_id INTEGER PRIMARY KEY,
        guild_id INTEGER,channel_id INTEGER,updated_at TEXT,updated_by INTEGER);
    INSERT INTO alliance_list VALUES(5,'NewERA',100,1501);
    INSERT INTO alliancesettings VALUES(5,1010,1020);
    INSERT INTO troop_quality_channels VALUES(5,100,1030,'now',1);
    """)
    _db(s, """
    CREATE TABLE admin(id INTEGER PRIMARY KEY,is_initial INTEGER,is_owner INTEGER);
    CREATE TABLE adminserver(admin INTEGER,alliances_id INTEGER);
    CREATE TABLE admin_role(role_id INTEGER PRIMARY KEY,guild_id INTEGER,is_initial INTEGER);
    CREATE TABLE adminserver_role(role_id INTEGER,alliances_id INTEGER);
    CREATE TABLE alliance_logs(alliance_id INTEGER PRIMARY KEY,channel_id INTEGER);
    INSERT INTO admin VALUES(11,0,0);
    INSERT INTO admin VALUES(12,0,0);
    INSERT INTO admin VALUES(13,1,1);
    INSERT INTO adminserver VALUES(11,5);
    INSERT INTO adminserver VALUES(12,5);
    INSERT INTO adminserver VALUES(12,6);
    INSERT INTO admin_role VALUES(55,100,0);
    INSERT INTO adminserver_role VALUES(55,5);
    INSERT INTO alliance_logs VALUES(5,1040);
    """)
    _db(u, """
    CREATE TABLE users(fid INTEGER PRIMARY KEY,alliance TEXT,discord_id INTEGER,
        discord_server_id INTEGER,nickname TEXT);
    CREATE TABLE player_sync_changes(id INTEGER PRIMARY KEY,alliance_id INTEGER,
        field TEXT);
    INSERT INTO users VALUES(123,'5',987,100,'Alice');
    INSERT INTO users VALUES(234,'5',NULL,NULL,'Bob');
    INSERT INTO player_sync_changes VALUES(1,5,'Power');
    """)
    _db(i, """
    CREATE TABLE id_channels(guild_id INTEGER,alliance_id INTEGER,channel_id INTEGER,
        created_at TEXT,created_by INTEGER);
    INSERT INTO id_channels VALUES(100,5,1000,'now',1);
    """)
    _db(m, """
    CREATE TABLE temporary_alliances(id INTEGER PRIMARY KEY,guild_id INTEGER,
        state_id INTEGER,game_alliance_id INTEGER,tag TEXT,scope_alliance_id INTEGER,
        enabled INTEGER,created_by INTEGER,created_at TEXT,
        UNIQUE(guild_id,state_id,game_alliance_id,scope_alliance_id));
    INSERT INTO temporary_alliances VALUES(1,100,1501,200,'S01',5,1,1,'now');
    """)
    _db(g, """
    CREATE TABLE giftcode_channel(alliance_id INTEGER PRIMARY KEY,channel_id INTEGER);
    INSERT INTO giftcode_channel VALUES(5,1050);
    """)
    for key,path in (("A",a),("S",s),("U",u),("I",i),("M",m),("G",g)):
        monkeypatch.setattr(svc,key,str(path))
    monkeypatch.chdir(tmp_path)
    return a,s,u,i,m,g


def _row(file,sql):
    with sqlite3.connect(file) as db:
        return db.execute(sql).fetchall()


def test_transfer_retains_members_history_and_revokes_only_scoped_grants(registry):
    a,s,u,i,m,g=registry
    snapshot=svc.inspect(5)
    assert snapshot["source"]==100 and snapshot["members"]==2
    backup=svc.transfer(5,expected_source=100,destination=200,actor_id=13,
                        mappings={"id":2000,"log":2040,"redeem":2020,
                                  "sync":2010,"troop":2030,"gift":2050})
    assert Path(backup).is_dir()
    assert _row(a,"SELECT alliance_id,discord_server_id FROM alliance_list")==[(5,200)]
    assert _row(u,"SELECT fid,alliance,discord_id,discord_server_id FROM users ORDER BY fid")==[
        (123,'5',987,None),(234,'5',None,None)]
    assert _row(u,"SELECT alliance_id,field FROM player_sync_changes")==[(5,'Power')]
    assert _row(s,"SELECT id FROM admin ORDER BY id")==[(12,),(13,)]
    assert _row(s,"SELECT alliances_id FROM adminserver")==[(6,)]
    assert _row(s,"SELECT role_id FROM admin_role")==[]
    assert _row(i,"SELECT guild_id,alliance_id,channel_id FROM id_channels")==[(200,5,2000)]
    assert _row(m,"SELECT guild_id,scope_alliance_id FROM temporary_alliances")==[(200,5)]
    assert _row(a,"SELECT guild_id,channel_id FROM troop_quality_channels")==[(200,2030)]
    assert _row(g,"SELECT alliance_id,channel_id FROM giftcode_channel")==[(5,2050)]


def test_source_mismatch_never_changes_database(registry):
    a,s,u,i,m,g=registry
    with pytest.raises(ValueError,match="changed"):
        svc.transfer(5,expected_source=777,destination=200,actor_id=13,
                     mappings={k:None for k in svc.KINDS})
    assert _row(a,"SELECT discord_server_id FROM alliance_list")==[(100,)]
    assert _row(u,"SELECT COUNT(*) FROM users")==[(2,)]


def test_explicit_destination_role_replaces_old_guild_role(registry):
    a,s,u,i,m,g=registry
    svc.transfer(5,expected_source=100,destination=200,actor_id=13,
                 admin_role_id=888,
                 mappings={"id":2000,"log":2040,"redeem":2020,
                           "sync":2010,"troop":2030,"gift":2050})
    assert _row(s,"SELECT role_id,guild_id,is_initial FROM admin_role")==[(888,200,0)]
    assert _row(s,"SELECT role_id,alliances_id FROM adminserver_role")==[(888,5)]


def test_shared_destination_channel_validates_and_transfers(registry):
    from types import SimpleNamespace
    a,s,u,i,m,g = registry
    perms = SimpleNamespace(view_channel=True, send_messages=True,
                            embed_links=True, read_message_history=True)
    channel = SimpleNamespace(permissions_for=lambda member: perms, send=lambda: None)
    dest = SimpleNamespace(id=200, me=object(), get_channel=lambda cid: channel if cid == 2000 else None)
    bot = SimpleNamespace(get_guild=lambda gid: object() if gid == 100 else None)
    mappings = dict.fromkeys(svc.KINDS, 2000)
    plan = {**svc.inspect(5), "mappings": mappings}
    assert svc.validate(plan, dest, bot)
    perms.read_message_history = False
    with pytest.raises(ValueError, match="Read Message History"):
        svc.validate(plan, dest, bot)
    perms.read_message_history = True
    with sqlite3.connect(a) as db:
        db.execute("INSERT INTO alliance_list VALUES(6,'Other',200,1501)")
        db.execute("INSERT INTO alliancesettings VALUES(6,2000,NULL)")
    with pytest.raises(ValueError, match="another alliance"):
        svc.validate(plan, dest, bot)
    with sqlite3.connect(a) as db:
        db.execute("DELETE FROM alliancesettings WHERE alliance_id=6")
    assert svc.validate(plan, dest, bot)
    svc.transfer(5, expected_source=100, destination=200, actor_id=13, mappings=mappings)
    assert svc.inspect(5)["channels"] == mappings
