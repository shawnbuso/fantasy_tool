"""Offensive snap counts.

Needed to tell "played and did nothing" apart from "didn't play". The stat feed only
carries players who recorded something, so a receiver who ran routes all game and was
never targeted is simply absent from it -- indistinguishable, without this, from one
who was inactive or on a bye.

Snap counts key on Pro Football Reference ids, so they need crosswalking to the gsis
ids everything else uses. Coverage starts in 2012; earlier seasons come back empty.
"""

import nflreadpy as nfl
import polars as pl

FIRST_SEASON = 2012
COLUMNS = ("offense_snaps",)


def _crosswalk() -> pl.DataFrame:
    """pfr_id -> gsis_id, the only link between snap counts and everything else."""
    players = nfl.load_players()
    return (
        players.select(pl.col("pfr_id"), pl.col("gsis_id"))
        .filter(pl.col("pfr_id").is_not_null() & pl.col("gsis_id").is_not_null())
        .unique(subset=["pfr_id"])
    )


def rows(season: int) -> pl.DataFrame:
    """Offensive snaps per (player_id, week), or an empty frame before 2012."""
    schema = {
        "season": pl.Int32,
        "week": pl.Int32,
        "player_id": pl.String,
        "offense_snaps": pl.Float64,
    }
    if season < FIRST_SEASON:
        return pl.DataFrame(schema=schema)

    counts = nfl.load_snap_counts(seasons=[season]).filter(pl.col("game_type") == "REG")
    return (
        counts.join(_crosswalk(), left_on="pfr_player_id", right_on="pfr_id", how="inner")
        .group_by(["season", "week", "gsis_id"])
        .agg(pl.col("offense_snaps").sum())
        .rename({"gsis_id": "player_id"})
        .select(
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            pl.col("player_id"),
            pl.col("offense_snaps").cast(pl.Float64),
        )
    )
