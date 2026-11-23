"""Regression tests for proposed dated, single-session minister bookings."""
import sqlite3
from datetime import datetime, timedelta, timezone
import pytest
from cogs import minister_self_service as service


@pytest.fixture
def config(tmp_path,monkeypatch):
    booking=tmp_path/"svs.sqlite"
    users=tmp_path/"users.sqlite"
    monkeypatch.setattr(service,"DB",str(booking))
    monkeypatch.setattr(service,"USERS_DB",str(users))
    with sqlite3.connect(users) as db:
        db.execute("CREATE TABLE users(fid INTEGER PRIMARY KEY,nickname TEXT,alliance INTEGER,"
                   "discord_id INTEGER,discord_server_id INTEGER)")
        db.executemany("INSERT INTO users VALUES(?,?,?,?,?)",[
            (11,"One",2,101,501),
            (12,"Two",2,102,501),
            (13,"Three",3,103,501)])
    with sqlite3.connect(booking) as db:
        db.execute("CREATE TABLE appointments("
                   "fid INTEGER,appointment_type TEXT,time TEXT,alliance INTEGER,"
                   "PRIMARY KEY(fid,appointment_type))")
        service.migrate(db)
        day=(datetime.now(timezone.utc)+timedelta(days=3)).date().isoformat()
        db.executemany(
            "INSERT INTO minister_event_dates VALUES(?,?,?,?)",
            [(typ,day,1,999) for typ in service.TYPES])
        db.executemany(
            "INSERT INTO minister_player_approvals VALUES(?,?,?,?,?)",
            [(11,101,501,999,day),(12,102,501,999,day)])
    return str(booking)


def read(db,query,*params):
    with sqlite3.connect(db) as conn:
        return conn.execute(query,params).fetchall()


def test_one_session_three_ministers_with_same_time(config):
    choices={typ:"14:30" for typ in service.TYPES}
    service.book_many(101,501,11,choices)
    assert len(read(config,"SELECT * FROM appointments WHERE fid=?",11))==3


def test_move_only_own_booking_into_free_slot(config):
    service.book_many(101,501,11,{"Research Day":"14:30"})
    service.book_many(101,501,11,{"Research Day":"15:00"})
    assert read(config,"SELECT time FROM appointments WHERE fid=?",11)==[("15:00",)]


def test_cannot_take_reserved_slot_or_lose_old_one(config):
    service.book_many(101,501,11,{"Research Day":"14:30"})
    service.book_many(102,501,12,{"Research Day":"15:00"})
    with pytest.raises(ValueError,match="already been booked"):
        service.book_many(101,501,11,{"Research Day":"15:00"})
    assert read(config,"SELECT time FROM appointments WHERE fid=?",11)==[("14:30",)]


def test_combined_booking_is_all_or_nothing(config):
    service.book_many(102,501,12,{"Research Day":"15:00"})
    with pytest.raises(ValueError,match="already been booked"):
        service.book_many(101,501,11,{"Construction Day":"09:00","Research Day":"15:00"})
    assert read(config,"SELECT * FROM appointments WHERE fid=?",11)==[]


def test_unapproved_character_rejected(config):
    with pytest.raises(PermissionError):
        service.book_many(103,501,13,{"Research Day":"12:00"})


def test_other_discord_member_rejected(config):
    with pytest.raises(PermissionError):
        service.book_many(102,501,11,{"Research Day":"12:00"})


def test_legacy_admin_booking_cannot_be_moved_by_member(config):
    with sqlite3.connect(config) as db:
        db.execute("INSERT INTO appointments VALUES(11,'Research Day','14:30',2)")
    with pytest.raises(PermissionError,match="administrator-controlled"):
        service.book_many(101,501,11,{"Research Day":"15:00"})
    assert read(config,"SELECT time FROM appointments WHERE fid=?",11)==[("14:30",)]


def test_unique_constraint_blocks_second_occupant(config):
    service.book_many(101,501,11,{"Research Day":"14:30"})
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(config) as db:
            db.execute("INSERT INTO appointments VALUES(12,'Research Day','14:30',2)")


def test_standard_slots_are_48_and_finish_at_2330():
    x=service.slots()
    assert len(x)==48
    assert x[0]=="00:00" and x[-1]=="23:30"
    assert "14:30" in x


def test_existing_duplicate_slots_block_member_writes(config):
    with sqlite3.connect(config) as db:
        db.execute("DROP INDEX minister_unique_occupied_time")
        db.execute("INSERT INTO appointments VALUES(11,'Research Day','09:00',2)")
        db.execute("INSERT INTO appointments VALUES(12,'Research Day','09:00',2)")
    with pytest.raises(ValueError,match="duplicate"):
        service.book_many(101,501,11,{"Construction Day":"08:00"})
