"""Verification for the Yahoo matchup parser.

Runs against a trimmed but genuine page from the league's own 2024 season -- the two
stat tables lifted verbatim out of a live fetch. Synthetic markup would prove nothing
here; the whole risk is that Yahoo's real structure differs from what was expected.
"""

from pathlib import Path

import pytest

from fantasy_tool.sources.yahoo import parse

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_matchup_2024_w05.html"


@pytest.fixture(scope="module")
def matchup() -> parse.Matchup:
    return parse.parse_file(FIXTURE, week=5)


def test_reconciles_with_yahoos_own_total(matchup: parse.Matchup) -> None:
    """The check that makes the rest trustworthy.

    The page mirrors both teams around a centre column, so reading one column too far
    left or right yields the opponent's points and a perfectly plausible-looking
    lineup. Yahoo prints its own total, so agreement is proof the mapping is right.
    """
    assert matchup.reconciles()
    assert matchup.total() == pytest.approx(242.30)
    assert matchup.total(away=True) == pytest.approx(194.14)


def test_starters_and_bench_are_separated(matchup: parse.Matchup) -> None:
    assert len(matchup.starters()) == 9
    assert len(matchup.starters(away=True)) == 9
    assert any(s.slot == "BN" for s in matchup.home)
    assert all(s.slot != "BN" for s in matchup.starters())


def test_slots_are_read_from_the_centre_column(matchup: parse.Matchup) -> None:
    slots = [s.slot for s in matchup.starters()]
    assert slots == ["QB", "WR", "WR", "RB", "RB", "TE", "W/R/T", "K", "DEF"]


def test_players_carry_their_yahoo_id(matchup: parse.Matchup) -> None:
    """Ids come from the profile link. Names collide and change; ids don't."""
    quarterback = matchup.starters()[0]
    assert quarterback.name == "Baker Mayfield"
    assert quarterback.yahoo_id == "30971"


def test_both_teams_are_named(matchup: parse.Matchup) -> None:
    assert matchup.home_team == "Bobalki Bandits"
    assert matchup.away_team == "Shots of Slivovitz"


def test_an_unplayed_game_is_none_not_zero(matchup: parse.Matchup) -> None:
    """Yahoo writes an absence as an en dash. A real zero is a performance."""
    assert parse._number("–") is None
    assert parse._number("0.00") == 0.0
    unplayed = [s for s in matchup.home + matchup.away if s.points is None]
    assert unplayed, "expected at least one player who didn't play"


def test_bench_total_does_not_masquerade_as_the_score(matchup: parse.Matchup) -> None:
    """Both tables print a total row; only the starters' one is the team's score."""
    assert matchup.reported_home == pytest.approx(242.30)
    assert matchup.reported_home != pytest.approx(54.64)  # the bench total


def test_a_mangled_page_fails_to_reconcile() -> None:
    """The guard has to be able to fail, or it isn't a guard."""
    broken = parse.Matchup(
        week=1,
        home_team="a",
        away_team="b",
        home=(parse.Slot("QB", "1", "x", 10.0, 9.0),),
        away=(),
        reported_home=99.0,
    )
    assert not broken.reconciles()
