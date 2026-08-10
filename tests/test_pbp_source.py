"""Verification for the play-by-play derived categories.

The failure mode here is silent zero, not a crash: a filter keyed off the wrong flag
produces a perfectly well-formed column of nothing, and a rule built on that category
would simply never fire. Two real bugs found this way are pinned below.
"""

import polars as pl
import pytest

from fantasy_tool import store
from fantasy_tool.sources.pbp import PLAYER_STATS, TEAM_STATS

SEASON = 2024
# Seasons already in the store; used for the rare-category checks where one season
# isn't enough to distinguish "genuinely rare" from "silently broken".
SPAN = range(2018, 2025)


@pytest.fixture(scope="session")
def frame() -> pl.DataFrame:
    store.sync([SEASON])
    return store.load_frame(SEASON)


def test_all_pbp_columns_present(frame: pl.DataFrame) -> None:
    for column in PLAYER_STATS + TEAM_STATS:
        assert column in frame.columns, column


def test_no_negative_counts(frame: pl.DataFrame) -> None:
    totals = frame.select(PLAYER_STATS + TEAM_STATS).min().to_dicts()[0]
    assert all(v >= 0 for v in totals.values()), totals


def test_long_passing_and_receiving_tds_almost_agree(frame: pl.DataFrame) -> None:
    """Nearly every long touchdown pass has one passer and one receiver -- but not all.

    On a lateral the receiver isn't the scorer, so the play counts for the passer and
    not the receiver. Those are rare, so the two totals should be close but the
    receiving side must never exceed the passing side.
    """
    totals = frame.select(["passing_tds_40", "receiving_tds_40"]).sum().to_dicts()[0]
    passing, receiving = totals["passing_tds_40"], totals["receiving_tds_40"]
    assert passing > 0
    assert receiving <= passing
    assert passing - receiving <= 0.05 * passing, totals


def test_pick_sixes_match_defensive_side(frame: pl.DataFrame) -> None:
    """A pick six charged to a quarterback is the same play the defense scored on."""
    thrown = frame["pick_sixes_thrown"].sum()
    scored = frame.filter(pl.col("position") == "DEF")["def_interception_return_td"].sum()
    assert thrown == scored > 0


def test_rare_categories_are_populated() -> None:
    """Regression guard for two silent-zero bugs.

    `def_blocked_return_td` keyed off `return_touchdown`, which nflverse never sets on
    a blocked kick (it codes them as recoveries). `def_extra_point_returned` keyed off
    `defensive_extra_point_conv`, a column present in the schema but never populated;
    the real data lives in `defensive_two_point_conv`. Both produced clean zeros.
    """
    available = [s for s in SPAN if store.season_path(store.DEFAULT_ROOT, s).exists()]
    if len(available) < 3:
        pytest.skip("needs several synced seasons")

    totals = {"def_blocked_return_td": 0.0, "def_extra_point_returned": 0.0}
    for season in available:
        defenses = store.load_frame(season).filter(pl.col("position") == "DEF")
        for key in totals:
            totals[key] += defenses[key].sum()

    for key, total in totals.items():
        assert total > 0, f"{key} is zero across {len(available)} seasons -- likely broken"


def test_three_and_outs_are_plausible(frame: pl.DataFrame) -> None:
    """Roughly two per team-game; wildly off means the drive grouping is wrong."""
    defenses = frame.filter(pl.col("position") == "DEF")
    per_team_week = defenses["def_three_and_outs"].sum() / defenses.height
    assert 1.0 <= per_team_week <= 4.0, per_team_week


def test_long_tds_do_not_exceed_long_plays(frame: pl.DataFrame) -> None:
    """A 40+ yard touchdown is by definition also a 40+ yard play."""
    bad = frame.filter(pl.col("receiving_tds_40") > pl.col("receiving_40"))
    assert bad.height == 0, bad.select("name", "week", "receiving_40", "receiving_tds_40").head()


def test_defensive_touchdowns_cover_every_type(frame: pl.DataFrame) -> None:
    """Yahoo's D/ST "Touchdown" means any defensive score, not just pick sixes.

    nflverse's own def_tds counts interception returns only -- it matched our pick-six
    total exactly, to the row -- so fumble and blocked-kick returns were dropped and
    the category scored roughly half the touchdowns it should. Kick and punt returns
    stay out on purpose: Yahoo scores those under a category of their own.
    """
    defenses = frame.filter(pl.col("position") == "DEF")
    parts = (
        pl.col("def_interception_return_td")
        + pl.col("def_fumble_return_td")
        + pl.col("def_blocked_return_td")
    )
    assert defenses.filter(parts != pl.col("def_tds")).height == 0
    # Each type must actually contribute, or we're back to counting one of them.
    for column in ("def_interception_return_td", "def_fumble_return_td"):
        assert defenses[column].sum() > 0
    assert defenses["def_tds"].sum() > defenses["def_interception_return_td"].sum()
    # Return touchdowns are a separate category and must not be folded in.
    assert defenses["def_return_tds"].sum() > 0
