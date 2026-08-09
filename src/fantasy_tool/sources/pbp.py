"""Categories that only play-by-play can answer.

Yahoo exposes thirteen scoring categories the weekly tables can't reconstruct: long
touchdowns (as opposed to long plays), touchdowns split by how the ball was turned
over, and drive-level defensive stops. All of them need the individual plays.

Runs at sync time only, and adds one ~20MB download per season.
"""

import nflreadpy as nfl
import polars as pl

# What we credit to the individual offensive player.
PLAYER_STATS = (
    "passing_tds_40",
    "receiving_tds_40",
    "rushing_tds_40",
    "pick_sixes_thrown",
    "off_fumble_return_td",
)

# What we credit to the team defense/special-teams unit.
TEAM_STATS = (
    "def_4th_down_stops",
    "def_three_and_outs",
    "def_extra_point_returned",
    "def_kickoff_return_td",
    "def_punt_return_td",
    "def_interception_return_td",
    "def_fumble_return_td",
    "def_blocked_return_td",
)

LONG_TD_YARDS = 40


def _count(plays: pl.DataFrame, mask: pl.Expr, by: str, name: str) -> pl.DataFrame:
    """Count matching plays per (season, week, actor), dropping unattributed plays."""
    return (
        plays.filter(mask & pl.col(by).is_not_null())
        .group_by(["season", "week", by])
        .len(name=name)
        .rename({by: "actor"})
        .with_columns(pl.col(name).cast(pl.Float64))
    )


def _merge(frames: list[pl.DataFrame], names: tuple[str, ...]) -> pl.DataFrame:
    """Outer-join per-stat counts into one row per (season, week, actor)."""
    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.join(frame, on=["season", "week", "actor"], how="full", coalesce=True)
    return merged.with_columns([pl.col(n).fill_null(0.0) for n in names])


def player_rows(season: int) -> pl.DataFrame:
    """Long-touchdown and turnover categories, per player-week."""
    plays = nfl.load_pbp(seasons=[season]).filter(pl.col("season_type") == "REG")
    long_play = pl.col("yards_gained") >= LONG_TD_YARDS

    # Laterals make "who gained the yards" and "who scored" different people. Goff to
    # St. Brown for 1 yard, lateral to Williams for 41 and the score, is a 42-yard
    # passing touchdown but NOT a 40-yard receiving touchdown for St. Brown. So the
    # ball-carrier categories additionally require that the player scored the points.
    scored_it = pl.col("td_player_id").is_not_null()

    frames = [
        _count(
            plays, (pl.col("pass_touchdown") == 1) & long_play, "passer_player_id", "passing_tds_40"
        ),
        _count(
            plays,
            (pl.col("pass_touchdown") == 1)
            & long_play
            & scored_it
            & (pl.col("receiver_player_id") == pl.col("td_player_id")),
            "receiver_player_id",
            "receiving_tds_40",
        ),
        _count(
            plays,
            (pl.col("rush_touchdown") == 1)
            & long_play
            & scored_it
            & (pl.col("rusher_player_id") == pl.col("td_player_id")),
            "rusher_player_id",
            "rushing_tds_40",
        ),
        # Charged to the quarterback who threw it.
        _count(
            plays,
            (pl.col("interception") == 1) & (pl.col("return_touchdown") == 1),
            "passer_player_id",
            "pick_sixes_thrown",
        ),
        # An offensive player recovering his own team's fumble and scoring it himself.
        _count(
            plays,
            (pl.col("fumble") == 1)
            & (pl.col("touchdown") == 1)
            & (pl.col("fumble_recovery_1_team") == pl.col("posteam"))
            & scored_it
            & (pl.col("fumble_recovery_1_player_id") == pl.col("td_player_id")),
            "fumble_recovery_1_player_id",
            "off_fumble_return_td",
        ),
    ]
    return _merge(frames, PLAYER_STATS).rename({"actor": "player_id"})


def team_rows(season: int) -> pl.DataFrame:
    """Defensive stop and return-touchdown categories, per team-week."""
    plays = nfl.load_pbp(seasons=[season]).filter(pl.col("season_type") == "REG")
    returned_td = pl.col("return_touchdown") == 1

    # Three and out: a drive that produced no first down and ended in a punt. Defined
    # by first downs rather than play count, since a penalty can stretch a drive past
    # three snaps without ever moving the chains.
    drives = (
        plays.filter(pl.col("fixed_drive").is_not_null() & pl.col("defteam").is_not_null())
        .group_by(["season", "week", "game_id", "fixed_drive"])
        .agg(
            pl.col("defteam").first(),
            pl.col("fixed_drive_result").first(),
            pl.col("drive_first_downs").max(),
        )
    )
    three_and_outs = (
        drives.filter(
            (pl.col("fixed_drive_result") == "Punt")
            & (pl.col("drive_first_downs").fill_null(0) == 0)
        )
        .group_by(["season", "week", "defteam"])
        .len(name="def_three_and_outs")
        .rename({"defteam": "actor"})
        .with_columns(pl.col("def_three_and_outs").cast(pl.Float64))
    )

    frames = [
        _count(plays, pl.col("fourth_down_failed") == 1, "defteam", "def_4th_down_stops"),
        three_and_outs,
        # Yahoo's "Extra Point Returned" is the defense taking back a PAT try for two.
        # nflverse files that under defensive_two_point_*; defensive_extra_point_* is
        # present in the schema but never populated in any season.
        _count(
            plays, pl.col("defensive_two_point_conv") == 1, "defteam", "def_extra_point_returned"
        ),
        # Scoring credit follows td_team, which stays correct when the scoring team
        # isn't the defense on the play -- a muffed punt recovered and returned, say.
        _count(
            plays,
            (pl.col("play_type") == "kickoff") & returned_td,
            "td_team",
            "def_kickoff_return_td",
        ),
        _count(
            plays, (pl.col("play_type") == "punt") & returned_td, "td_team", "def_punt_return_td"
        ),
        _count(
            plays,
            (pl.col("interception") == 1) & returned_td,
            "td_team",
            "def_interception_return_td",
        ),
        _count(
            plays, (pl.col("fumble_lost") == 1) & returned_td, "td_team", "def_fumble_return_td"
        ),
        # Deliberately keys off `touchdown`, not `return_touchdown`: nflverse scores a
        # blocked kick picked up and run in as a recovery, never as a return, so the
        # return flag is always 0 here and this category would silently stay empty.
        _count(
            plays,
            ((pl.col("punt_blocked") == 1) | (pl.col("field_goal_result") == "blocked"))
            & (pl.col("touchdown") == 1),
            "td_team",
            "def_blocked_return_td",
        ),
    ]
    return _merge(frames, TEAM_STATS).rename({"actor": "player_id"})
