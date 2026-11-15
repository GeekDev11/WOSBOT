"""Whiteout Stats leaderboard parsing and command safeguards."""
import pytest

from cogs.wos_leaderboards import BOARDS, parse_board, summary_text, BOARD_AUTOCOMPLETE


def test_all_26_categories_available():
    assert len(BOARDS) == 26
    assert "master_power" in BOARDS
    assert "personal_power" in BOARDS


@pytest.mark.asyncio
async def test_autocomplete_includes_last_category():
    options = await BOARD_AUTOCOMPLETE(None, "master")
    assert [x.value for x in options] == ["master_power"]


def test_parse_player_board():
    result = parse_board({"ok": True, "rankings": [
        {"rank": 1, "chief_id": 137940156, "nick_name": "Test", "score": 1000000}
    ]})
    assert result[0]["chief_id"] == 137940156
    assert result[0]["score"] == 1000000
    assert "#1" in summary_text(result)


def test_parse_alliance_board():
    result = parse_board({"ok": True, "entries": [
        {"rank": 1, "aid": 20, "abbr": "ABC", "name": "Alliance", "score": 100}
    ]})
    assert result[0]["alliance_id"] == 20


@pytest.mark.parametrize("bad", [{}, None, [], {"ok": False}, {"entries": None}])
def test_unexpected_board_never_claims_zero_players(bad):
    with pytest.raises(ValueError):
        parse_board(bad)


def test_empty_list_is_valid_board():
    assert parse_board({"rankings": []}) == []


def test_summary_limits_to_15_rows():
    entries = [{"rank": i + 1, "name": f"Player {i}", "score": i} for i in range(30)]
    assert len(summary_text(entries).splitlines()) == 15


from cogs.wos_leaderboards import (
    SCOUT_BOARDS, SCOUT_EXCLUDE, SCOUT_LIMIT, parse_state_boards,
    scout_description, StateScoutView,
)


def test_scout_excludes_furnace_and_both_kills():
    assert len(SCOUT_BOARDS) == 23
    assert SCOUT_EXCLUDE == {"furnace", "kills", "alliance_kills"}
    assert not (set(SCOUT_BOARDS) & SCOUT_EXCLUDE)
    assert SCOUT_LIMIT == 20


def test_scout_combined_dict_only_recognized_boards():
    payload = {"ok": True, "state": {"boards": {
        "personal_power": [{"rank": 1, "chief_id": 42, "nick_name": "Chief", "score": 100}],
        "alliance_power": {"entries": [{"rank": 1, "aid": 7, "abbr": "VEN", "score": 50}]},
        "kills": [{"rank": 1, "chief_id": 2, "score": 1000}],
    }}}
    found = parse_state_boards(payload)
    assert set(found) == {"personal_power", "alliance_power"}
    assert found["personal_power"][0]["chief_id"] == 42
    assert found["alliance_power"][0]["alliance_id"] == 7


def test_scout_combined_list_and_missing_response_are_safe():
    data = {"boards": [
        {"board": "pet_power", "entries": [{"rank": 1, "chief_id": 7, "score": 123}]},
        {"board": "furnace", "entries": [{"rank": 1, "chief_id": 7, "score": 80}]},
    ]}
    assert set(parse_state_boards(data)) == {"pet_power"}
    assert parse_state_boards({"state": {"boards": []}}) == {}
    assert parse_state_boards({"ok": False, "boards": {"pet_power": []}}) == {}


def test_scout_displays_all_20_rows():
    entries = [{"rank": i, "name": f"Chief{i}", "score": 100000-i}
               for i in range(1, 25)]
    out = scout_description(entries)
    assert "#20" in out
    assert "#21" not in out


def test_scout_ui_has_23_options_and_navigation():
    class Dummy:
        pass
    view = StateScoutView(Dummy(), 1501, 1234)
    assert len(view.category_selector.options) == 23
    assert all(opt.value not in SCOUT_EXCLUDE for opt in view.category_selector.options)
    assert view.next_button.disabled is False
    assert view.prev_button.disabled is False


def test_scout_registered_for_guild_and_dm_installs():
    from cogs.wos_leaderboards import WosStateScout
    import discord
    command = WosStateScout.scout
    assert command.allowed_installs is not None
    assert command.allowed_installs.guild is True
    assert command.allowed_installs.user is True
    assert command.allowed_contexts is not None
    assert command.allowed_contexts.guild is True
    assert command.allowed_contexts.dm_channel is True
