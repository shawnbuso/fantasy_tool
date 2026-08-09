"""Build StatLines from nflreadpy.

This module runs only at `sync` time. Everything expensive or network-bound lives
here: downloading nflverse parquet, deriving the fields nflreadpy doesn't ship, and
flattening it all into one wide table per season. Simulations read the persisted
result and never call into here.

Naming note: canonical stat keys are nflreadpy's column names wherever one exists,
so the mapping stays obvious. Derived keys are spelled out in DERIVED below.
"""

import nflreadpy as nfl
import polars as pl

from . import pbp

# Straight passthroughs from load_player_stats(summary_level="week").
_PLAYER_PASSTHROUGH = (
    # Passing
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "sacks_suffered",
    "passing_first_downs",
    "passing_40",
    # Rushing
    "carries",
    "rushing_yards",
    "rushing_tds",
    "rushing_first_downs",
    "rushing_40",
    # Receiving
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "receiving_first_downs",
    "receiving_40",
    # Misc offense
    "fumbles_total",
    "fumbles_lost_total",
    "special_teams_tds",
    "punt_returns",
    "punt_return_yards",
    "kickoff_returns",
    "kickoff_return_yards",
    # Kicking
    "fg_made",
    "fg_att",
    "fg_missed",
    "fg_blocked",
    "fg_long",
    "fg_made_0_19",
    "fg_made_20_29",
    "fg_made_30_39",
    "fg_made_40_49",
    "fg_made_50_59",
    "fg_made_60_",
    "fg_missed_0_19",
    "fg_missed_20_29",
    "fg_missed_30_39",
    "fg_missed_40_49",
    "fg_missed_50_59",
    "fg_missed_60_",
    "pat_made",
    "pat_att",
    "pat_missed",
    # IDP
    "def_tackles_solo",
    "def_tackle_assists",
    "def_tackles_for_loss",
    "def_fumbles_forced",
    "def_sacks",
    "def_interceptions",
    "def_pass_defended",
    "def_tds",
    "def_safeties",
    "fumble_recovery_opp",
    "fumble_recovery_yards_opp",
)

# Yahoo scores points-allowed and yards-allowed as independent on/off categories
# rather than as one tiered lookup -- each band is its own row in the settings UI
# with its own point value. Encoding them as 0/1 indicators means scoring stays a
# plain sum of multiplier * stat, with no tier machinery anywhere. The band edges
# are fixed by Yahoo and are not league-configurable.
POINTS_ALLOWED_BANDS = (
    ("pa_0", None, 0),
    ("pa_1_6", 1, 6),
    ("pa_7_13", 7, 13),
    ("pa_14_20", 14, 20),
    ("pa_21_27", 21, 27),
    ("pa_28_34", 28, 34),
    ("pa_35_plus", 35, None),
)

YARDS_ALLOWED_BANDS = (
    ("ya_negative", None, -1),
    ("ya_0_99", 0, 99),
    ("ya_100_199", 100, 199),
    ("ya_200_299", 200, 299),
    ("ya_300_399", 300, 399),
    ("ya_400_499", 400, 499),
    ("ya_500_plus", 500, None),
)

# Keys we compute; see _player_rows / _defense_rows for the arithmetic.
DERIVED = (
    "incompletions",
    "two_pt_conversions",
    "return_yards",
    "fg_made_50_",
    "fg_missed_50_",
    "fg_total_yards",
    "def_points_allowed",
    "def_yards_allowed",
    "def_fumble_recoveries",
    "def_blocked_kicks",
    "def_return_yards",
    "def_return_tds",
    *[name for name, _, _ in POINTS_ALLOWED_BANDS],
    *[name for name, _, _ in YARDS_ALLOWED_BANDS],
    *pbp.PLAYER_STATS,
    *pbp.TEAM_STATS,
)


def _band_indicator(source: str, lo: int | None, hi: int | None) -> pl.Expr:
    """1.0 when `source` falls inside [lo, hi], else 0.0. Either bound may be open."""
    test = pl.lit(True)
    if lo is not None:
        test = test & (pl.col(source) >= lo)
    if hi is not None:
        test = test & (pl.col(source) <= hi)
    return pl.when(test).then(1.0).otherwise(0.0)


STAT_COLUMNS = tuple(_PLAYER_PASSTHROUGH) + DERIVED
ID_COLUMNS = ("player_id", "name", "position", "nfl_team", "season", "week", "opponent")
EVENT_COLUMNS = ("fg_made_yards", "fg_missed_yards")

_FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K")


def _yard_list(col: str) -> pl.Expr:
    """Parse nflreadpy's semicolon-delimited kick-distance strings into int lists.

    Note the delimiter is ';' despite the column being named *_list -- e.g. "29;31".
    """
    return (
        pl.when(pl.col(col).is_null() | (pl.col(col) == ""))
        .then(pl.lit([], dtype=pl.List(pl.Int32)))
        .otherwise(pl.col(col).str.split(";").cast(pl.List(pl.Int32)))
    )


def _player_rows(season: int) -> pl.DataFrame:
    df = nfl.load_player_stats(seasons=[season], summary_level="week").filter(
        (pl.col("season_type") == "REG") & pl.col("position").is_in(_FANTASY_POSITIONS)
    )

    return df.select(
        pl.col("player_id"),
        pl.col("player_display_name").alias("name"),
        pl.col("position"),
        pl.col("team").alias("nfl_team"),
        pl.col("season"),
        pl.col("week"),
        pl.col("opponent_team").alias("opponent"),
        *[pl.col(c).cast(pl.Float64) for c in _PLAYER_PASSTHROUGH],
        # Yahoo scores incompletions as their own category (stat_id 3).
        (pl.col("attempts") - pl.col("completions")).cast(pl.Float64).alias("incompletions"),
        # Yahoo has ONE combined 2-point category (16); nflreadpy splits it three ways.
        (
            pl.col("passing_2pt_conversions")
            + pl.col("rushing_2pt_conversions")
            + pl.col("receiving_2pt_conversions")
        )
        .cast(pl.Float64)
        .alias("two_pt_conversions"),
        # Yahoo's offensive "Return Yards" (14) is punt and kick combined.
        (pl.col("punt_return_yards") + pl.col("kickoff_return_yards"))
        .cast(pl.Float64)
        .alias("return_yards"),
        # Yahoo has ONE 50+ bucket (23/28); nflreadpy splits 50-59 and 60+.
        (pl.col("fg_made_50_59") + pl.col("fg_made_60_")).cast(pl.Float64).alias("fg_made_50_"),
        (pl.col("fg_missed_50_59") + pl.col("fg_missed_60_"))
        .cast(pl.Float64)
        .alias("fg_missed_50_"),
        # Yahoo's per-yard FG scoring (84).
        _yard_list("fg_made_list").list.sum().cast(pl.Float64).alias("fg_total_yards"),
        _yard_list("fg_made_list").alias("fg_made_yards"),
        _yard_list("fg_missed_list").alias("fg_missed_yards"),
    )


def _points_allowed(season: int) -> pl.DataFrame:
    """Per team-week points allowed, from final scores.

    Convention: this counts ALL points the opposing NFL team scored, including their
    defensive and special-teams touchdowns. That matches Yahoo -- a pick-six thrown by
    the quarterback your D/ST is facing still counts against your D/ST.
    """
    sch = nfl.load_schedules(seasons=[season]).filter(pl.col("game_type") == "REG")
    home = sch.select(
        pl.col("season"),
        pl.col("week"),
        pl.col("home_team").alias("team"),
        pl.col("away_score").cast(pl.Float64).alias("def_points_allowed"),
    )
    away = sch.select(
        pl.col("season"),
        pl.col("week"),
        pl.col("away_team").alias("team"),
        pl.col("home_score").cast(pl.Float64).alias("def_points_allowed"),
    )
    return pl.concat([home, away])


def _defense_rows(season: int) -> pl.DataFrame:
    """Team D/ST units, assembled from team stats + the opponent's offense + scores."""
    ts = nfl.load_team_stats(seasons=[season], summary_level="week").filter(
        pl.col("season_type") == "REG"
    )

    # What the opponent did on offense, and what they had blocked against them.
    opp = ts.select(
        pl.col("season"),
        pl.col("week"),
        pl.col("team").alias("opponent"),
        # Total net yards, the official convention: gross passing yards, less sack
        # yardage, plus rushing. Note nflreadpy stores sack_yards_lost as a NEGATIVE
        # number, so this adds it. Verified against box scores -- e.g. BAL wk1 2024
        # is 273 + (-6) + 185 = 452.
        (pl.col("passing_yards") + pl.col("sack_yards_lost") + pl.col("rushing_yards"))
        .cast(pl.Float64)
        .alias("def_yards_allowed"),
        (pl.col("fg_blocked") + pl.col("pat_blocked") + pl.col("pt_blocked"))
        .cast(pl.Float64)
        .alias("def_blocked_kicks"),
    )

    own = ts.select(
        pl.col("team").alias("player_id"),
        (pl.col("team") + pl.lit(" DEF")).alias("name"),
        pl.lit("DEF").alias("position"),
        pl.col("team").alias("nfl_team"),
        pl.col("season"),
        pl.col("week"),
        pl.col("opponent_team").alias("opponent"),
        pl.col("def_sacks").cast(pl.Float64),
        pl.col("def_interceptions").cast(pl.Float64),
        pl.col("def_tds").cast(pl.Float64),
        pl.col("def_safeties").cast(pl.Float64),
        pl.col("def_tackles_for_loss").cast(pl.Float64),
        # Recoveries of the opponent's fumbles.
        pl.col("fumble_recovery_opp").cast(pl.Float64).alias("def_fumble_recoveries"),
        (pl.col("punt_return_yards") + pl.col("kickoff_return_yards"))
        .cast(pl.Float64)
        .alias("def_return_yards"),
        # Yahoo also exposes these split out, under DEF/ST Miscellaneous.
        pl.col("punt_return_yards").cast(pl.Float64),
        pl.col("kickoff_return_yards").cast(pl.Float64),
        pl.col("special_teams_tds").cast(pl.Float64).alias("def_return_tds"),
    )

    scores = _points_allowed(season).rename({"team": "player_id"})
    joined = own.join(opp, on=["season", "week", "opponent"], how="left").join(
        scores, on=["season", "week", "player_id"], how="left"
    )

    return joined.with_columns(
        *[
            _band_indicator("def_points_allowed", lo, hi).alias(name)
            for name, lo, hi in POINTS_ALLOWED_BANDS
        ],
        *[
            _band_indicator("def_yards_allowed", lo, hi).alias(name)
            for name, lo, hi in YARDS_ALLOWED_BANDS
        ],
    )


def build_season(season: int) -> pl.DataFrame:
    """One wide table of every scorable player-week and team-week in a season."""
    players = _player_rows(season).join(
        pbp.player_rows(season), on=["season", "week", "player_id"], how="left"
    )
    defense = _defense_rows(season).join(
        pbp.team_rows(season), on=["season", "week", "player_id"], how="left"
    )
    combined = pl.concat([players, defense], how="diagonal")

    # Every stat column present and non-null, so downstream code never guards.
    missing = [c for c in STAT_COLUMNS if c not in combined.columns]
    combined = combined.with_columns([pl.lit(0.0).alias(c) for c in missing])
    return combined.select(
        *ID_COLUMNS,
        *[pl.col(c).cast(pl.Float64).fill_null(0.0) for c in STAT_COLUMNS],
        *[
            pl.col(c).fill_null(pl.lit([], dtype=pl.List(pl.Int32)))
            if c in combined.columns
            else pl.lit([], dtype=pl.List(pl.Int32)).alias(c)
            for c in EVENT_COLUMNS
        ],
    ).sort("week", "position", "player_id")
