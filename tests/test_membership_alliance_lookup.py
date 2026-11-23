"""Temporary alliance registration resolves AID from WhiteoutStats, not from users."""
import asyncio
from types import SimpleNamespace

import pytest

from cogs import membership_alliance_lookup as lookup
from cogs import membership_tracking as tracking


def test_validated_alliance_requires_exact_state_tag_and_aid():
    result=lookup.validated_alliance(
        {"ok":True,"fresh":True,
         "alliance":{"aid":932244,"kid":1501,"abbr":"S01","name":"Gathering"}},
        1501,"S01")
    assert (result.state_id,result.aid,result.tag,result.name)==(1501,932244,"S01","Gathering")
    with pytest.raises(ValueError,match="different state"):
        lookup.validated_alliance({"alliance":{"aid":123,"kid":920,"abbr":"S01"}},1501,"S01")
    with pytest.raises(ValueError,match="different alliance tag"):
        lookup.validated_alliance({"alliance":{"aid":123,"kid":1501,"abbr":"s01"}},1501,"S01")
    with pytest.raises(ValueError,match="valid Alliance ID"):
        lookup.validated_alliance({"alliance":{"aid":None,"kid":1501,"abbr":"S01"}},1501,"S01")
    with pytest.raises(ValueError,match="refreshing"):
        lookup.validated_alliance({"fresh":False,"alliance":{"aid":1,"kid":1501,"abbr":"S01"}},1501,"S01")


def test_modal_requires_only_visible_state_and_tag():
    modal=tracking.RegistryModal(None,1501)
    assert [field.label for field in modal.children]==[
        "State ID","Alliance tag (case-sensitive)"]
    assert len(modal.children)==2


def test_tracking_view_uses_sync_now_label():
    view=tracking.TrackingView(None,1)
    labels={getattr(child,"label","") for child in view.children}
    assert "Sync Now" in labels
    assert "Reconcile Now" not in labels


class FakeResponse:
    status=200
    def __init__(self,payload):
        self.payload=payload
    async def __aenter__(self):
        return self
    async def __aexit__(self,*args):
        pass
    async def json(self,content_type=None):
        return self.payload


class FakeSession:
    def __init__(self,payload):
        self.payload=payload
        self.requests=[]
    def get(self,url,**kwargs):
        self.requests.append((url,kwargs))
        return FakeResponse(self.payload)


def test_lookup_uses_existing_connection_budget_and_verifies_result(monkeypatch):
    session=FakeSession({"ok":True,"alliance":{
        "aid":789123,"abbr":"300","kid":1501,"name":"State Gathering"}})
    cog=SimpleNamespace(session=lambda:session)
    bot=SimpleNamespace(get_cog=lambda name:cog)
    called=[]
    async def slot(profile):
        called.append(profile)
        return True
    monkeypatch.setattr(lookup.sync,"load_connection",lambda:(
        {"base_url":"https://api.whiteoutstats.com","auth_header":"X-Api-Key"},"test-secret"))
    monkeypatch.setattr(lookup.sync,"_take_slot",slot)
    result,error=asyncio.run(lookup.resolve(bot,1501,"300"))
    assert error is None and result.aid==789123 and result.tag=="300"
    assert len(called)==1
    assert session.requests[0][0]=="https://api.whiteoutstats.com/v1/alliances/1501/300"
    assert session.requests[0][1]["params"]=={"include":"info","wait":30}
    assert session.requests[0][1]["headers"]["X-Api-Key"]=="test-secret"


def test_missing_key_never_consumes_request_budget(monkeypatch):
    monkeypatch.setattr(lookup.sync,"load_connection",lambda:(
        {"base_url":"https://api.whiteoutstats.com","auth_header":"X-Api-Key"},""))
    async def no_slot(profile):
        raise AssertionError("No request should be charged without a key")
    monkeypatch.setattr(lookup.sync,"_take_slot",no_slot)
    bot=SimpleNamespace(get_cog=lambda name:SimpleNamespace())
    result,error=asyncio.run(lookup.resolve(bot,1501,"S01"))
    assert result is None and "Configure" in error


def test_no_response_and_unknown_alliance_do_not_register(monkeypatch):
    session=FakeSession({})
    session.get=lambda url,**kwargs: type("Missing", (FakeResponse,),{"status":404})({})
    cog=SimpleNamespace(session=lambda:session)
    bot=SimpleNamespace(get_cog=lambda name:cog)
    monkeypatch.setattr(lookup.sync,"load_connection",lambda:(
        {"base_url":"https://api.whiteoutstats.com","auth_header":"X-Api-Key"},"key"))
    async def slot(profile):
        return True
    monkeypatch.setattr(lookup.sync,"_take_slot",slot)
    result,error=asyncio.run(lookup.resolve(bot,1501,"S01"))
    assert result is None and "could not verify" in error


def test_ranked_alliance_requires_exact_case_aid_and_single_match():
    payload = {"ok": True, "kid": 1501, "rankings": [
        {"aid": 43210, "abbr": "S01", "name": "Example", "rank": 3, "score": 1000},
        {"aid": 87654, "abbr": "s01", "name": "Different", "rank": 4, "score": 500},
    ]}
    resolved = lookup.validated_ranked_alliance(payload, 1501, "S01")
    assert resolved and resolved.aid == 43210 and resolved.state_id == 1501
    assert lookup.validated_ranked_alliance(payload, 1502, "S01") is None
    assert lookup.validated_ranked_alliance(payload, 1501, "SO1") is None
    payload["rankings"].append(
        {"aid": 99999, "abbr": "S01", "rank": 5, "score": 200}
    )
    assert lookup.validated_ranked_alliance(payload, 1501, "S01") is None


def test_direct_404_falls_back_to_verified_state_ranking(monkeypatch):
    class RoutedSession:
        def __init__(self):
            self.requests = []

        def get(self, url, **kwargs):
            self.requests.append((url, kwargs))
            if "/v1/alliances/" in url:
                response = FakeResponse({})
                response.status = 404
                return response
            response = FakeResponse({
                "ok": True, "kid": 1501,
                "rankings": [{"aid": 777, "abbr": "S01", "name": "Example",
                              "rank": 1, "score": 1000}],
            })
            return response

    session = RoutedSession()
    bot = SimpleNamespace(get_cog=lambda name: SimpleNamespace(session=lambda: session))
    monkeypatch.setattr(lookup.sync, "load_connection", lambda: (
        {"base_url": "https://api.whiteoutstats.com", "auth_header": "X-Api-Key"}, "test-key"
    ))
    calls = []
    async def slot(profile):
        calls.append(1)
        return True
    monkeypatch.setattr(lookup.sync, "_take_slot", slot)
    result, error = asyncio.run(lookup.resolve(bot, 1501, "S01"))
    assert error is None and result.aid == 777
    assert len(calls) == 2
    assert session.requests[1][1]["params"] == {"board": "alliance_power", "limit": 100}
