"""Regression tests for proposed dated, single-session minister bookings."""
import sqlite3
from datetime import datetime, timedelta, timezone
import pytest
from cogs import minister_self_service as service


@pytest.fixture
def config(tmp_path,monkeypatch):
    booking=tmp_path/"svs.sqlite"
    users=tmp_path/"users.sqlite"
    alliances=tmp_path/"alliance.sqlite"
    monkeypatch.setattr(service,"DB",str(booking))
    monkeypatch.setattr(service,"USERS_DB",str(users))
    monkeypatch.setattr(service,"ALLIANCE_DB",str(alliances))
    with sqlite3.connect(alliances) as db:
        db.execute("CREATE TABLE alliance_list(alliance_id INTEGER PRIMARY KEY,name TEXT,discord_server_id INTEGER)")
        db.executemany("INSERT INTO alliance_list VALUES(?,?,?)",[(2,"VEN",501),(3,"ABC",501),(4,"OTHER",999)])
    with sqlite3.connect(users) as db:
        db.execute("CREATE TABLE users(fid INTEGER PRIMARY KEY,nickname TEXT,alliance INTEGER,"
                   "discord_id INTEGER,discord_server_id INTEGER)")
        db.executemany("INSERT INTO users VALUES(?,?,?,?,?)",[
            (11,"One",2,101,501),
            (12,"Two",2,102,501),
            (13,"Three",3,103,501),
            (14,"Four",2,None,None),
            (15,"Other Server",4,None,None)])
    with sqlite3.connect(booking) as db:
        db.execute("CREATE TABLE appointments("
                   "fid INTEGER,appointment_type TEXT,time TEXT,alliance INTEGER,"
                   "PRIMARY KEY(fid,appointment_type))")
        service.migrate(db)
        db.execute("INSERT INTO minister_reminder_channels VALUES(501,999)")
        day=(datetime.now(timezone.utc)+timedelta(days=3)).date().isoformat()
        db.executemany(
            "INSERT INTO minister_event_dates VALUES(?,?,?,?)",
            [(typ,(datetime.fromisoformat(day)+timedelta(days=i)).date().isoformat(),1,999)
             for i,typ in enumerate(service.TYPES)])
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


def test_roster_member_does_not_need_minister_approval(config):
    service.book_many(103,501,13,{"Research Day":"12:00"})
    assert read(config,"SELECT time FROM appointments WHERE fid=?",13)==[("12:00",)]


def test_other_discord_member_rejected(config):
    with pytest.raises(PermissionError):
        service.book_many(102,501,11,{"Research Day":"12:00"})


def test_verified_member_can_adopt_legacy_admin_booking(config):
    with sqlite3.connect(config) as db:
        db.execute("INSERT INTO appointments VALUES(11,'Research Day','14:30',2)")
    service.book_many(101,501,11,{"Research Day":"15:00"})
    assert read(config,"SELECT time FROM appointments WHERE fid=?",11)==[("15:00",)]
    assert read(config,"SELECT discord_id FROM minister_booking_owners WHERE fid=?",11)==[(101,)]


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


def test_offset_slots_follow_legacy_mode():
    x=service.slots(1)
    assert len(x)==49
    assert x[0]=="00:00" and x[1]=="00:15" and x[-1]=="23:45"


def test_same_day_overlapping_minister_types_are_rejected(config):
    with sqlite3.connect(config) as db:
        day=db.execute("SELECT event_date FROM minister_event_dates WHERE appointment_type='Research Day'").fetchone()[0]
        db.execute("UPDATE minister_event_dates SET event_date=? WHERE appointment_type='Construction Day'",(day,))
    with pytest.raises(ValueError,match="overlap"):
        service.book_many(101,501,11,{"Construction Day":"14:30","Research Day":"14:30"})
    assert read(config,"SELECT COUNT(*) FROM appointments")[0][0]==0


def test_unlinked_roster_fid_first_successful_booking_claims_id(config):
    member=service.lookup_for_member(14,501,101)
    assert member["name"]=="Four" and member["alliance_name"]=="VEN"
    assert service.book_many(101,501,14,{"Research Day":"12:30"})=={"Research Day":None}
    assert read(config,"SELECT discord_id,claim_source FROM minister_player_claims WHERE fid=?",14)==[
        (101,"first_reservation")
    ]
    with pytest.raises(PermissionError,match="claimed"):
        service.lookup_for_member(14,501,102)
    with pytest.raises(PermissionError,match="claimed"):
        service.book_many(102,501,14,{"Research Day":"13:00"})
    assert read(config,"SELECT time FROM appointments WHERE fid=?",14)==[("12:30",)]


def test_failed_booking_never_claims_roster_fid(config):
    service.book_many(101,501,11,{"Research Day":"12:30"})
    with pytest.raises(ValueError,match="already been booked"):
        service.book_many(102,501,14,{"Research Day":"12:30"})
    assert read(config,"SELECT COUNT(*) FROM minister_player_claims WHERE fid=?",14)==[(0,)]


def test_member_cannot_claim_existing_discord_link(config):
    with pytest.raises(PermissionError,match="linked"):
        service.lookup_for_member(13,501,101)


def test_different_server_cannot_search_or_book_roster_fid(config):
    with pytest.raises(ValueError,match="not found"):
        service.lookup_for_member(15,501,101)
    with pytest.raises(ValueError,match="no longer in an alliance"):
        service.book_many(101,501,15,{"Research Day":"12:30"})


def test_admin_transfer_recovers_wrong_first_claim_and_reminders(config):
    service.book_many(101,501,14,{"Research Day":"12:30"})
    old=service.transfer_claim(14,501,102,999)
    assert old==101
    assert read(config,"SELECT discord_id FROM minister_player_claims WHERE fid=?",14)==[(102,)]
    assert read(config,"SELECT discord_id FROM minister_booking_owners WHERE fid=?",14)==[(102,)]
    assert read(config,"SELECT old_discord_id,new_discord_id,changed_by FROM minister_claim_audit")==[
        (101,102,999)
    ]
    with pytest.raises(PermissionError):
        service.book_many(101,501,14,{"Research Day":"13:00"})
    service.book_many(102,501,14,{"Research Day":"13:00"})
    assert read(config,"SELECT time FROM appointments WHERE fid=?",14)==[("13:00",)]


def test_first_claim_cannot_move_unowned_legacy_admin_reservation(config):
    with sqlite3.connect(config) as db:
        db.execute("INSERT INTO appointments VALUES(14,'Research Day','12:30',2)")
    with pytest.raises(PermissionError,match="older administrator-created"):
        service.book_many(101,501,14,{"Research Day":"13:00"})
    assert read(config,"SELECT COUNT(*) FROM minister_player_claims WHERE fid=?",14)==[(0,)]
    assert read(config,"SELECT time FROM appointments WHERE fid=?",14)==[("12:30",)]


def test_prior_admin_approval_not_silently_overridden(config):
    with sqlite3.connect(config) as db:
        db.execute("INSERT INTO minister_player_approvals VALUES(14,102,501,999,'2026-01-01')")
    with pytest.raises(PermissionError,match="administrator-approved"):
        service.book_many(101,501,14,{"Research Day":"12:30"})


def test_same_player_no_duplicate_booking_after_claim(config):
    service.book_many(101,501,14,{"Research Day":"12:30"})
    service.book_many(101,501,14,{"Research Day":"13:00"})
    assert read(config,"SELECT COUNT(*) FROM appointments WHERE fid=?",14)==[(1,)]


def test_member_menu_accepts_roster_id_without_registration(config):
    import asyncio
    from types import SimpleNamespace
    sent=[]
    async def send_message(**kwargs):
        sent.append(kwargs)
    fake=SimpleNamespace(
        user=SimpleNamespace(id=101),
        guild_id=501,
        response=SimpleNamespace(send_message=send_message)
    )
    cog=object.__new__(service.MinisterSelfService)
    cog.bot=SimpleNamespace()
    asyncio.run(service.MinisterSelfService.open_player(cog,fake,14))
    assert sent and sent[-1]["ephemeral"] is True
    assert "Four" in sent[-1]["embed"].description
    assert "VEN" in sent[-1]["embed"].description
    assert isinstance(sent[-1]["view"],service.ConfirmMinisterCharacter)


def test_member_menu_unknown_fid_is_rejected(config):
    import asyncio
    from types import SimpleNamespace
    sent=[]
    async def send_message(*args,**kwargs):
        sent.append((args,kwargs))
    fake=SimpleNamespace(
        user=SimpleNamespace(id=101),
        guild_id=501,
        response=SimpleNamespace(send_message=send_message)
    )
    cog=object.__new__(service.MinisterSelfService)
    cog.bot=SimpleNamespace()
    asyncio.run(service.MinisterSelfService.open_player(cog,fake,999999))
    assert sent and "not found" in sent[0][0][0]


def test_designated_minister_server_can_look_up_other_alliance_roster(config):
    with sqlite3.connect(config) as db:
        db.execute("CREATE TABLE reference(context TEXT PRIMARY KEY,context_id INTEGER)")
        db.execute("INSERT INTO reference VALUES('minister guild id',501)")
    member=service.lookup_for_member(15,501,101)
    assert member["name"]=="Other Server"
    assert member["alliance_name"]=="OTHER"
    service.book_many(101,501,15,{"Research Day":"17:00"})
    assert read(config,"SELECT time FROM appointments WHERE fid=?",15)==[("17:00",)]


def test_returning_member_can_resume_claimed_fid_without_retyping(config):
    service.book_many(101,501,14,{"Research Day":"13:00"})
    known=service.known_player_ids(101,501)
    assert 14 in {m["fid"] for m in known}
    assert 12 not in {m["fid"] for m in known}


def test_member_cannot_see_other_accounts_claimed_characters(config):
    service.book_many(101,501,14,{"Research Day":"13:00"})
    assert 14 not in {m["fid"] for m in service.known_player_ids(102,501)}
