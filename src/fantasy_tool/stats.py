"""The stat registry: a text mirror of Yahoo's Scoring Settings page.

Every row Yahoo renders appears here once, in the same order and with Yahoo's own
label, so `fantasy-tool stats` reads like the settings page and a YAML file can be
checked against it key by key. Transcribed from the commissioner settings page of
the Four Finger Family league (ID 786986), 2026 season.

`column` is the store column the value comes from. `needs_pbp=True` marks categories
Yahoo supports that we cannot yet score, because they need play-by-play detail the
weekly tables don't carry -- enabling one is a loud error rather than a silent zero.
"""

from dataclasses import dataclass

OFF = frozenset({"QB", "RB", "WR", "TE"})
K = frozenset({"K"})
DST = frozenset({"DEF"})

# Yahoo caps how many offensive categories a league may enable at once.
MAX_OFFENSE_CATEGORIES = 26

# Yahoo allows bonuses on these three categories only, three tiers each.
BONUS_ELIGIBLE = ("passing_yards", "rushing_yards", "receiving_yards")
MAX_BONUSES_PER_CATEGORY = 3

# Categories measured in yards. The league-wide `fractional_points` and
# `negative_points` switches apply to these and to nothing else -- Yahoo's help is
# explicit that they govern "categories that track yards earned or lost".
YARDAGE_KEYS = frozenset(
    {
        "passing_yards",
        "rushing_yards",
        "receiving_yards",
        "return_yards",
        "fg_total_yards",
        "def_return_yards",
        "def_kickoff_return_yards",
        "def_punt_return_yards",
    }
)


@dataclass(frozen=True, slots=True)
class Stat:
    key: str  # YAML key
    label: str  # Yahoo's UI label, verbatim
    section: str  # top-level heading on the settings page
    group: str  # sub-heading within the section
    positions: frozenset[str]
    column: str | None  # store column; None when unscoreable
    needs_pbp: bool = False

    @property
    def supported(self) -> bool:
        return self.column is not None

    @property
    def is_team_defense(self) -> bool:
        """Scored for a team D/ST unit rather than an individual player.

        This is the only scoring boundary that matters. Yahoo's Offense / Kickers
        headings are how the settings page is organised, not a restriction on who can
        earn a category: a kicker who throws a touchdown on a fake still gets the
        passing points. A team unit, though, is a different kind of entity entirely.
        """
        return self.positions == DST


# Order matches the settings page top to bottom.
STATS: tuple[Stat, ...] = (
    # ---------------------------------------------------------------- OFFENSE
    Stat("passing_tds", "Passing Touchdowns", "Offense", "Passing", OFF, "passing_tds"),
    Stat(
        "passing_interceptions", "Interceptions", "Offense", "Passing", OFF, "passing_interceptions"
    ),
    Stat("attempts", "Passing Attempts", "Offense", "Passing", OFF, "attempts"),
    Stat("completions", "Completions", "Offense", "Passing", OFF, "completions"),
    Stat("sacks_suffered", "Sacks", "Offense", "Passing", OFF, "sacks_suffered"),
    Stat("passing_yards", "Passing Yards", "Offense", "Passing", OFF, "passing_yards"),
    Stat("pick_sixes_thrown", "Pick Sixes Thrown", "Offense", "Passing", OFF, "pick_sixes_thrown"),
    Stat("passing_40", "40+ Yard Completions", "Offense", "Passing", OFF, "passing_40"),
    Stat(
        "passing_tds_40", "40+ Yard Passing Touchdowns", "Offense", "Passing", OFF, "passing_tds_40"
    ),
    Stat(
        "passing_first_downs", "Passing 1st Downs", "Offense", "Passing", OFF, "passing_first_downs"
    ),
    Stat("rushing_tds", "Rushing Touchdowns", "Offense", "Rushing", OFF, "rushing_tds"),
    Stat("carries", "Rushing Attempts", "Offense", "Rushing", OFF, "carries"),
    Stat("rushing_yards", "Rushing Yards", "Offense", "Rushing", OFF, "rushing_yards"),
    Stat("rushing_40", "40+ Yard Run", "Offense", "Rushing", OFF, "rushing_40"),
    Stat(
        "rushing_tds_40", "40+ Yard Rushing Touchdowns", "Offense", "Rushing", OFF, "rushing_tds_40"
    ),
    Stat(
        "rushing_first_downs", "Rushing 1st Downs", "Offense", "Rushing", OFF, "rushing_first_downs"
    ),
    Stat("receiving_tds", "Receiving Touchdowns", "Offense", "Receiving", OFF, "receiving_tds"),
    Stat("receptions", "Receptions", "Offense", "Receiving", OFF, "receptions"),
    Stat("receiving_yards", "Receiving Yards", "Offense", "Receiving", OFF, "receiving_yards"),
    Stat("receiving_40", "40+ Yard Receptions", "Offense", "Receiving", OFF, "receiving_40"),
    Stat(
        "receiving_tds_40",
        "40+ Yard Receiving Touchdowns",
        "Offense",
        "Receiving",
        OFF,
        "receiving_tds_40",
    ),
    Stat(
        "receiving_first_downs",
        "Receiving 1st Downs",
        "Offense",
        "Receiving",
        OFF,
        "receiving_first_downs",
    ),
    Stat(
        "special_teams_tds",
        "Return Touchdowns",
        "Offense",
        "Kick and Punt Returning",
        OFF,
        "special_teams_tds",
    ),
    Stat("return_yards", "Return Yards", "Offense", "Kick and Punt Returning", OFF, "return_yards"),
    Stat(
        "two_pt_conversions",
        "2-Point Conversions",
        "Offense",
        "Miscellaneous",
        OFF,
        "two_pt_conversions",
    ),
    Stat("fumbles_total", "Fumbles", "Offense", "Miscellaneous", OFF, "fumbles_total"),
    Stat(
        "fumbles_lost_total", "Fumbles Lost", "Offense", "Miscellaneous", OFF, "fumbles_lost_total"
    ),
    Stat(
        "off_fumble_return_td",
        "Offensive Fumble Return TD",
        "Offense",
        "Miscellaneous",
        OFF,
        "off_fumble_return_td",
    ),
    # ---------------------------------------------------------------- KICKERS
    Stat("fg_made_0_19", "Field Goals 0-19 Yards", "Kickers", "Field Goals", K, "fg_made_0_19"),
    Stat("fg_made_20_29", "Field Goals 20-29 Yards", "Kickers", "Field Goals", K, "fg_made_20_29"),
    Stat("fg_made_30_39", "Field Goals 30-39 Yards", "Kickers", "Field Goals", K, "fg_made_30_39"),
    Stat("fg_made_40_49", "Field Goals 40-49 Yards", "Kickers", "Field Goals", K, "fg_made_40_49"),
    Stat("fg_made_50_", "Field Goals 50+ Yards", "Kickers", "Field Goals", K, "fg_made_50_"),
    Stat(
        "fg_missed_0_19",
        "Field Goals Missed 0-19 Yards",
        "Kickers",
        "Field Goals",
        K,
        "fg_missed_0_19",
    ),
    Stat(
        "fg_missed_20_29",
        "Field Goals Missed 20-29 Yards",
        "Kickers",
        "Field Goals",
        K,
        "fg_missed_20_29",
    ),
    Stat(
        "fg_missed_30_39",
        "Field Goals Missed 30-39 Yards",
        "Kickers",
        "Field Goals",
        K,
        "fg_missed_30_39",
    ),
    Stat(
        "fg_missed_40_49",
        "Field Goals Missed 40-49 Yards",
        "Kickers",
        "Field Goals",
        K,
        "fg_missed_40_49",
    ),
    Stat(
        "fg_missed_50_",
        "Field Goals Missed 50+ Yards",
        "Kickers",
        "Field Goals",
        K,
        "fg_missed_50_",
    ),
    Stat(
        "fg_total_yards",
        "Field Goals Total Yards",
        "Kickers",
        "Field Goal Yards Per Point",
        K,
        "fg_total_yards",
    ),
    Stat("pat_made", "Point After Attempt Made", "Kickers", "PAT", K, "pat_made"),
    Stat("pat_missed", "Point After Attempt Missed", "Kickers", "PAT", K, "pat_missed"),
    # ------------------------------------------------- DEFENSE/SPECIAL TEAMS
    Stat("pa_0", "Points Allowed 0 points", "Defense/Special Teams", "Points Allowed", DST, "pa_0"),
    Stat(
        "pa_1_6",
        "Points Allowed 1-6 points",
        "Defense/Special Teams",
        "Points Allowed",
        DST,
        "pa_1_6",
    ),
    Stat(
        "pa_7_13",
        "Points Allowed 7-13 points",
        "Defense/Special Teams",
        "Points Allowed",
        DST,
        "pa_7_13",
    ),
    Stat(
        "pa_14_20",
        "Points Allowed 14-20 points",
        "Defense/Special Teams",
        "Points Allowed",
        DST,
        "pa_14_20",
    ),
    Stat(
        "pa_21_27",
        "Points Allowed 21-27 points",
        "Defense/Special Teams",
        "Points Allowed",
        DST,
        "pa_21_27",
    ),
    Stat(
        "pa_28_34",
        "Points Allowed 28-34 points",
        "Defense/Special Teams",
        "Points Allowed",
        DST,
        "pa_28_34",
    ),
    Stat(
        "pa_35_plus",
        "Points Allowed 35+ points",
        "Defense/Special Teams",
        "Points Allowed",
        DST,
        "pa_35_plus",
    ),
    Stat("def_sacks", "Sack", "Defense/Special Teams", "Defense", DST, "def_sacks"),
    Stat(
        "def_interceptions",
        "Interception",
        "Defense/Special Teams",
        "Defense",
        DST,
        "def_interceptions",
    ),
    Stat(
        "def_fumble_recoveries",
        "Fumble Recovery",
        "Defense/Special Teams",
        "Defense",
        DST,
        "def_fumble_recoveries",
    ),
    Stat("def_tds", "Touchdown", "Defense/Special Teams", "Defense", DST, "def_tds"),
    Stat("def_safeties", "Safety", "Defense/Special Teams", "Defense", DST, "def_safeties"),
    Stat(
        "def_blocked_kicks",
        "Block Kick",
        "Defense/Special Teams",
        "Defense",
        DST,
        "def_blocked_kicks",
    ),
    Stat(
        "def_return_yards",
        "Return Yards",
        "Defense/Special Teams",
        "Defense",
        DST,
        "def_return_yards",
    ),
    Stat(
        "def_return_tds",
        "Kickoff and Punt Return Touchdowns",
        "Defense/Special Teams",
        "Defense",
        DST,
        "def_return_tds",
    ),
    Stat(
        "def_4th_down_stops",
        "4th Down Stops",
        "Defense/Special Teams",
        "Defense",
        DST,
        "def_4th_down_stops",
    ),
    Stat(
        "def_tackles_for_loss",
        "Tackles for Loss",
        "Defense/Special Teams",
        "Defense",
        DST,
        "def_tackles_for_loss",
    ),
    Stat(
        "ya_negative",
        "Defensive Yards Allowed - Negative",
        "Defense/Special Teams",
        "Yards Allowed",
        DST,
        "ya_negative",
    ),
    Stat(
        "ya_0_99",
        "Defensive Yards Allowed 0-99",
        "Defense/Special Teams",
        "Yards Allowed",
        DST,
        "ya_0_99",
    ),
    Stat(
        "ya_100_199",
        "Defensive Yards Allowed 100-199",
        "Defense/Special Teams",
        "Yards Allowed",
        DST,
        "ya_100_199",
    ),
    Stat(
        "ya_200_299",
        "Defensive Yards Allowed 200-299",
        "Defense/Special Teams",
        "Yards Allowed",
        DST,
        "ya_200_299",
    ),
    Stat(
        "ya_300_399",
        "Defensive Yards Allowed 300-399",
        "Defense/Special Teams",
        "Yards Allowed",
        DST,
        "ya_300_399",
    ),
    Stat(
        "ya_400_499",
        "Defensive Yards Allowed 400-499",
        "Defense/Special Teams",
        "Yards Allowed",
        DST,
        "ya_400_499",
    ),
    Stat(
        "ya_500_plus",
        "Defensive Yards Allowed 500+",
        "Defense/Special Teams",
        "Yards Allowed",
        DST,
        "ya_500_plus",
    ),
    Stat(
        "def_three_and_outs",
        "Three and Outs Forced",
        "Defense/Special Teams",
        "Defense",
        DST,
        "def_three_and_outs",
    ),
    Stat(
        "def_extra_point_returned",
        "Extra Point Returned",
        "Defense/Special Teams",
        "Defense",
        DST,
        "def_extra_point_returned",
    ),
    Stat(
        "def_kickoff_return_yards",
        "Kickoff Return Yards",
        "Defense/Special Teams",
        "Miscellaneous",
        DST,
        "kickoff_return_yards",
    ),
    Stat(
        "def_punt_return_yards",
        "Punt Return Yards",
        "Defense/Special Teams",
        "Miscellaneous",
        DST,
        "punt_return_yards",
    ),
    Stat(
        "def_kickoff_return_td",
        "Kickoff Return TD",
        "Defense/Special Teams",
        "Miscellaneous",
        DST,
        "def_kickoff_return_td",
    ),
    Stat(
        "def_punt_return_td",
        "Punt Return TD",
        "Defense/Special Teams",
        "Miscellaneous",
        DST,
        "def_punt_return_td",
    ),
    Stat(
        "def_interception_return_td",
        "Interception Return TD",
        "Defense/Special Teams",
        "Miscellaneous",
        DST,
        "def_interception_return_td",
    ),
    Stat(
        "def_fumble_return_td",
        "Fumble Return TD",
        "Defense/Special Teams",
        "Miscellaneous",
        DST,
        "def_fumble_return_td",
    ),
    Stat(
        "def_blocked_return_td",
        "Blocked Punt or FG Return TD",
        "Defense/Special Teams",
        "Miscellaneous",
        DST,
        "def_blocked_return_td",
    ),
)

STAT_BY_KEY: dict[str, Stat] = {s.key: s for s in STATS}
SECTIONS = ("Offense", "Kickers", "Defense/Special Teams")


def suggest(key: str) -> str:
    """Nearest known key, for 'did you mean' on a typo'd YAML entry."""
    from difflib import get_close_matches

    matches = get_close_matches(key, STAT_BY_KEY, n=1)
    return f" Did you mean {matches[0]!r}?" if matches else ""
