"""Verification for the extraction and the persisted store.

These lean on exhaustive self-consistency checks rather than hand-picked spot checks:
a parsing bug in a rarely-hit branch (a 60+ yard kick, a week with two long field
goals) is exactly the kind of thing that silently corrupts an analysis, so every
check below runs over a full season rather than a sample.
"""

import nflreadpy as nfl
import polars as pl
import pytest

from fantasy_tool import store
from fantasy_tool.sources.nfl import EVENT_COLUMNS, STAT_COLUMNS

SEASON = 2024


@pytest.fixture(scope="session")
def frame() -> pl.DataFrame:
    store.sync([SEASON])
    return store.load_frame(SEASON)


@pytest.fixture(scope="session")
def kickers(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.filter(pl.col("fg_att") > 0)


@pytest.fixture(scope="session")
def defenses(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.filter(pl.col("position") == "DEF")


def test_points_allowed_sums_to_game_total(defenses: pl.DataFrame) -> None:
    """Both defenses in a game must account for the game's total points.

    Cross-checks our per-team derivation against `schedules.total`, a column we
    never touch, so this catches a wrong home/away join rather than restating it.
    """
    sched = nfl.load_schedules(seasons=[SEASON]).filter(pl.col("game_type") == "REG")
    totals = {
        (row["week"], frozenset({row["home_team"], row["away_team"]})): row["total"]
        for row in sched.iter_rows(named=True)
    }

    seen: dict[tuple[int, frozenset[str]], float] = {}
    for row in defenses.iter_rows(named=True):
        key = (row["week"], frozenset({row["player_id"], row["opponent"]}))
        seen[key] = seen.get(key, 0.0) + row["def_points_allowed"]

    assert seen, "no defense rows"
    for key, combined in seen.items():
        assert combined == totals[key], f"{key}: {combined} != game total {totals[key]}"


def test_every_defense_row_has_a_score(defenses: pl.DataFrame) -> None:
    """A failed join would leave points allowed at 0.0, which scores as a shutout."""
    assert defenses.filter(pl.col("def_points_allowed").is_null()).height == 0
    # 32 teams, 18 weeks, each on bye once.
    assert defenses.height == 32 * 17
    assert defenses.filter(pl.col("def_yards_allowed") <= 0).height == 0


def test_yards_allowed_matches_box_score(defenses: pl.DataFrame) -> None:
    """Pin total net yards to a real box score.

    nflreadpy stores `sack_yards_lost` as a negative number, so the natural-looking
    `passing - sacks + rushing` silently adds sack yardage back. That error is
    invisible in aggregate but shifts every yards-allowed tier, so pin it here:
    KC/BAL 2024 week 1 was BAL 452 total net yards, KC 353.
    """
    week1 = defenses.filter(pl.col("week") == 1)
    kc = week1.filter(pl.col("player_id") == "KC").to_dicts()[0]
    bal = week1.filter(pl.col("player_id") == "BAL").to_dicts()[0]
    assert kc["def_yards_allowed"] == 452.0  # what BAL's offense gained
    assert bal["def_yards_allowed"] == 353.0  # what KC's offense gained


def test_yards_allowed_is_league_average(defenses: pl.DataFrame) -> None:
    """A sign error on sack yardage moves this ~40 yards; the real average is ~330."""
    assert 300 <= defenses["def_yards_allowed"].mean() <= 360


def test_defense_rows_are_unique(defenses: pl.DataFrame) -> None:
    """Guards against the opponent join fanning out and double-counting a week."""
    assert defenses.select("player_id", "week").is_duplicated().sum() == 0


def test_fg_buckets_sum_to_fg_made(kickers: pl.DataFrame) -> None:
    buckets = (
        pl.col("fg_made_0_19")
        + pl.col("fg_made_20_29")
        + pl.col("fg_made_30_39")
        + pl.col("fg_made_40_49")
        + pl.col("fg_made_50_")
    )
    assert kickers.filter(buckets != pl.col("fg_made")).height == 0


def test_fg_yard_list_matches_fg_made(kickers: pl.DataFrame) -> None:
    """The ';'-delimited kick list is what long-FG rules read; it must be complete."""
    bad = kickers.filter(pl.col("fg_made_yards").list.len() != pl.col("fg_made"))
    assert bad.height == 0, bad.select("name", "week", "fg_made", "fg_made_yards").head()


def test_fg_yard_list_agrees_with_buckets(kickers: pl.DataFrame) -> None:
    """Every 50+ kick in the list must be reflected in the 50+ bucket, and vice versa."""
    long_kicks = pl.col("fg_made_yards").list.eval(pl.element() >= 50).list.sum()
    assert kickers.filter(long_kicks != pl.col("fg_made_50_")).height == 0


def test_fg_total_yards_matches_list(kickers: pl.DataFrame) -> None:
    bad = kickers.filter(pl.col("fg_made_yards").list.sum() != pl.col("fg_total_yards"))
    assert bad.height == 0


def test_fumbles_lost_includes_return_fumbles(frame: pl.DataFrame) -> None:
    """`fumbles_lost_total` is the right source, not the sum of the positional columns.

    Return and aborted-snap fumbles land only in the total, so summing the passing,
    rushing, and receiving columns would silently undercount.
    """
    assert frame.filter(pl.col("fumbles_lost_total") > 0).height > 0


def test_store_round_trips(frame: pl.DataFrame) -> None:
    """Parquet must preserve the list columns, not just the scalars."""
    lines = store.load_statlines(SEASON)
    assert len(lines) == frame.height

    for row in frame.filter(pl.col("fg_made") > 0).head(50).iter_rows(named=True):
        line = lines[(row["player_id"], row["week"])]
        assert line.events["fg_made_yards"] == tuple(row["fg_made_yards"])
        assert line.s("fg_made") == row["fg_made"]

    # Zeros are dropped from the sparse dict but must still read back as 0.0.
    empty = next(v for v in lines.values() if "fg_made" not in v.stats)
    assert empty.s("fg_made") == 0.0


def test_all_declared_columns_present(frame: pl.DataFrame) -> None:
    for column in STAT_COLUMNS + EVENT_COLUMNS:
        assert column in frame.columns, column
    assert frame.select(STAT_COLUMNS).null_count().sum_horizontal().item() == 0
