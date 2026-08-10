"""Verification for assembling a real league-season from Yahoo pages.

Runs against a trimmed but genuine `/starters` page from the league's own 2024
season -- two team tables lifted verbatim from a live fetch.
"""

from pathlib import Path

import pytest

from fantasy_tool.sources.yahoo import season

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_starters_2024_w05.html"


@pytest.fixture(scope="module")
def lineups():
    return season.parse_starters(FIXTURE.read_text())


def test_every_team_gets_its_own_table(lineups) -> None:
    """The starters page is the whole league in one request, not one team."""
    assert {line.team_id for line in lineups} == {1, 5}
    assert {line.team_name for line in lineups} == {"Grundsau Power", "Bobalki Bandits"}


def test_starters_and_bench_are_distinguished(lineups) -> None:
    for line in lineups:
        assert len(line.started) == 9
        assert len(line.slots) == 15  # nine starters plus six bench
        assert all(slot != season.BENCH for slot, _, _ in line.started)


def test_slots_come_through_in_order(lineups) -> None:
    slots = [slot for slot, _, _ in lineups[0].started]
    assert slots == ["QB", "WR", "WR", "RB", "RB", "TE", "W/R/T", "K", "DEF"]


def test_players_carry_their_yahoo_id(lineups) -> None:
    by_team = {line.team_id: line for line in lineups}
    slot, yahoo_id, name = by_team[5].started[0]
    assert (slot, yahoo_id, name) == ("QB", "30971", "Baker Mayfield")


def test_urls_are_season_scoped() -> None:
    assert season.starters_url("583648", 5, 2024).endswith("/2024/f1/583648/starters?week=5")
    assert season.team_url("583648", 2, 5, 2024).endswith("/2024/f1/583648/2/team?week=5")


def test_opponent_is_read_from_the_team_page() -> None:
    """Yahoo's matchup page ignores the team id in the URL and always renders the
    logged-in user's own matchup, so the schedule has to come from team pages, each
    of which names the opponent it faced."""
    html = (
        '<div>Week 5 vs <a href="/2024/f1/583648/4">Butch’s First-Class Team</a>'
        " • 2 nd</div>"
    )
    assert season.parse_opponent(html) == (4, "Butch’s First-Class Team")


def test_no_opponent_is_reported_rather_than_guessed() -> None:
    assert season.parse_opponent("<div>nothing here</div>") is None


def test_relocated_franchises_keep_every_abbreviation() -> None:
    """A nickname can mean two teams: the Chargers are SD and LAC, the Raiders OAK
    and LV. Collapsing to one silently drops that defense from half the seasons."""
    mapping = season.defense_crosswalk()
    assert set(mapping["Chargers"]) >= {"LAC", "SD"}
    assert set(mapping["Raiders"]) >= {"LV", "OAK"}
    assert mapping["Ravens"] == ["BAL"]
