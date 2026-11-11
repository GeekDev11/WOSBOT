"""Tests for the optional Frosty SvS API response models."""
import pytest

from cogs.wos_svs import parse_matchup, parse_scores, show_score


def test_matchup():
    item = parse_matchup({
        "ok": True, "season": 22, "stage": "armament", "bye": False,
        "state": {"kid": 1}, "opponent": {"kid": 39},
    })
    assert item == {"season": 22, "stage": "armament", "bye": False, "state": 1, "opponent": 39}


def test_bye():
    item = parse_matchup({"ok": True, "state": {"kid": 12}, "opponent": None, "bye": True})
    assert item["bye"] and item["opponent"] is None


def test_scores():
    result = parse_scores({"ok": True, "state": 1, "opponent": 39,
        "score": 166483680, "opponent_score": 60111200,
        "days": [{"day": 1, "name": "I", "status": "In Progress", "state_score": 100}],
    })
    assert result["days"][0]["status"] == "In Progress"
    assert show_score(result["score"]) == "166,483,680"


@pytest.mark.parametrize("input", [{}, None, {"ok": False}, {"days": {}}])
def test_invalid_scores_are_rejected(input):
    with pytest.raises(ValueError):
        parse_scores(input)


@pytest.mark.parametrize("input", [{}, None, {"ok": False}, {"state": None}])
def test_invalid_matchups_are_rejected(input):
    with pytest.raises(ValueError):
        parse_matchup(input)
