import sqlite3
from types import SimpleNamespace as N
import pytest
from cogs import alliance_transfer_service as svc
from test_alliance_server_transfer import registry

class Guild:
    id=200
    me=object()
    def get_channel(self, cid):
        return N(send=lambda:None,permissions_for=lambda member:N(
            view_channel=True,send_messages=True,embed_links=True,read_message_history=True))

class Bot:
    def get_guild(self, gid): return object() if gid==100 else None

def test_tracking_target_required(registry):
    a,s,u,i,m,g=registry
    with sqlite3.connect(m) as db:
        db.execute("CREATE TABLE settings(guild_id INTEGER PRIMARY KEY,enabled INTEGER)")
        db.execute("INSERT INTO settings VALUES(100,1)")
    plan=svc.inspect(5)
    plan["mappings"]={"id":2000,"log":2040,"redeem":2020,
                      "sync":2010,"troop":2030,"gift":2050}
    with pytest.raises(ValueError,match="Enable Membership Tracking"):
        svc.validate(plan,Guild(),Bot())
    with sqlite3.connect(m) as db:
        db.execute("INSERT INTO settings VALUES(200,1)")
    assert svc.validate(plan,Guild(),Bot())
