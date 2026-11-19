"""Verified game alliance identities are distinct from local managed names."""
import sqlite3
from types import SimpleNamespace

import pytest

from cogs import alliance_identity as identity
from cogs.membership_alliance_lookup import validated_alliance
from cogs.alliance import AddAllianceModal


@pytest.fixture
def db(tmp_path,monkeypatch):
    path=tmp_path/"alliance.sqlite"
    monkeypatch.setattr(identity,"DB",str(path))
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE alliance_list("
                     "alliance_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     "name TEXT UNIQUE,discord_server_id INTEGER,kid INTEGER,state_locked INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE alliancesettings("
                     "alliance_id INTEGER,channel_id INTEGER,interval INTEGER)")
        conn.execute("INSERT INTO alliance_list(name,discord_server_id,kid,state_locked) "
                     "VALUES ('BikiniGangFarms',10,920,1)")
    identity.migrate()
    return path


def sample(tag="ven",aid=1234,state=920):
    return validated_alliance({
        "ok":True,"alliance":{"kid":state,"aid":aid,"abbr":tag,"name":"BikiniGangFarms"}
    },state,tag)


def test_existing_managed_alliance_can_be_verified_without_touching_state_lock(db):
    identity.save_verified(1,10,sample())
    assert identity.verified_identity(1)==(10,920,1234,"ven","BikiniGangFarms")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT state_locked FROM alliance_list WHERE alliance_id=1").fetchone()[0]==1
    identity.migrate()


def test_case_sensitive_exact_alliance_tag_verification():
    with pytest.raises(ValueError,match="different alliance tag"):
        validated_alliance({"alliance":{"kid":920,"aid":1234,"abbr":"VEN"}},920,"ven")
    assert sample("ven").tag=="ven"


def test_duplicate_verified_game_identity_cannot_be_linked_twice(db):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO alliance_list(name,discord_server_id,kid) VALUES ('Other',10,920)")
    identity.save_verified(1,10,sample())
    with pytest.raises(ValueError,match="already linked"):
        identity.save_verified(2,10,sample())
    with pytest.raises(ValueError,match="already registered"):
        identity.create_verified("Third",10,sample())


def test_wrong_home_state_or_guild_cannot_be_verified(db):
    with pytest.raises(ValueError,match="state or server changed"):
        identity.save_verified(1,11,sample())
    with pytest.raises(ValueError,match="state or server changed"):
        identity.save_verified(1,10,sample(state=921))


def test_new_managed_alliance_persists_verified_identity(db):
    resolved=sample("VEN",4321)
    created=identity.create_verified("New Managed Alliance",10,resolved)
    assert identity.verified_identity(created)==(
        10,920,4321,"VEN","New Managed Alliance")


def test_add_alliance_form_has_exact_tag_and_required_home_state():
    modal=AddAllianceModal(SimpleNamespace())
    assert [c.label for c in modal.children]==[
        "WOSBOT display name","State #","Exact in-game alliance tag"]
    assert all(c.required for c in modal.children)
