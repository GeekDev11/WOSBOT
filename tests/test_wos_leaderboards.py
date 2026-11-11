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
