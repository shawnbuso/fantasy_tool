"""Verification for the YAML schema and the base scorer.

The centrepiece is `test_matches_nflverse_ppr`: nflreadpy ships its own computed
fantasy points, so configuring a rule set to match its convention turns every
offensive player-week into a free assertion. That is a far better test of the scorer
than any hand-written example, because it exercises real edge cases at volume.
"""

from pathlib import Path

import nflreadpy as nfl
import polars as pl
import pytest
import yaml
from pydantic import ValidationError

from fantasy_tool import store
from fantasy_tool.model import StatLine
from fantasy_tool.scoring import RuleSet, load_ruleset, score_base

SEASON = 2024
SPAN = range(2018, 2025)
RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

# nflverse's own scoring convention, which differs from Yahoo's in two ways worth
# knowing: interceptions are -2 rather than -1, and fumbles count only the passing,
# rushing, and receiving columns rather than every fumble the player lost.
NFLVERSE_PPR = {
    "name": "nflverse PPR oracle",
    "lineup": {"starters": ["QB"], "bench": 0},
    "scoring": {
        "passing_yards": 0.04,
        "passing_tds": 4,
        "passing_interceptions": -2,
        "rushing_yards": 0.1,
        "rushing_tds": 6,
        "receiving_yards": 0.1,
        "receiving_tds": 6,
        "receptions": 1.0,
        "two_pt_conversions": 2,
        "special_teams_tds": 6,
        "fumbles_lost_total": -2,
    },
}


@pytest.fixture(scope="session")
def frame() -> pl.DataFrame:
    store.sync([SEASON])
    return store.load_frame(SEASON)


@pytest.fixture(scope="session")
def base() -> RuleSet:
    return load_ruleset(RULES_DIR / "base_yahoo.yaml")


def _line(position: str, **stats: float) -> StatLine:
    return StatLine(
        player_id="x",
        name="x",
        position=position,
        nfl_team="X",
        season=SEASON,
        week=1,
        opponent="Y",
        stats=stats,
    )


# --------------------------------------------------------------- the oracle


def test_matches_nflverse_ppr() -> None:
    """Score every offensive player-week and check it against nflverse's own column.

    Runs across every synced season, so the scorer is exercised on tens of thousands
    of real lines rather than a handful of examples.

    The only permitted disagreement is the fumble convention: nflverse sums the three
    positional fumble-lost columns, so a return or aborted-snap fumble -- which lands
    only in `fumbles_lost_total` -- costs us 2 points it doesn't charge. Every such
    row must be explained exactly by that difference, and no other row may differ.
    """
    rules = RuleSet(**NFLVERSE_PPR)
    seasons = [s for s in SPAN if store.season_path(store.DEFAULT_ROOT, s).exists()]
    assert seasons, "no synced seasons"

    checked = unexplained = fumble_gaps = 0
    for season in seasons:
        frame = store.load_frame(season).filter(pl.col("position") != "DEF")
        numeric = [
            c for c, dtype in zip(frame.columns, frame.dtypes, strict=True) if dtype == pl.Float64
        ]
        raw = nfl.load_player_stats(seasons=[season], summary_level="week").filter(
            pl.col("season_type") == "REG"
        )
        official = {
            (r["player_id"], r["week"]): r["fantasy_points_ppr"] for r in raw.iter_rows(named=True)
        }

        for row in frame.iter_rows(named=True):
            expected = official.get((row["player_id"], row["week"]))
            if expected is None:
                continue
            line = StatLine(
                player_id=row["player_id"],
                name=row["name"],
                position=row["position"],
                nfl_team=row["nfl_team"],
                season=row["season"],
                week=row["week"],
                opponent=row["opponent"] or "",
                stats={k: row[k] for k in numeric if row[k]},
            )
            checked += 1
            delta = score_base(line, rules) - expected
            if abs(delta) <= 0.01:
                continue
            # The rest must be exactly the return-fumble gap, and nothing else.
            positional = (
                row["sack_fumbles_lost"]
                + row["rushing_fumbles_lost"]
                + row["receiving_fumbles_lost"]
            )
            gap = row["fumbles_lost_total"] - positional
            if gap > 0 and abs(delta - (-2.0 * gap)) <= 0.01:
                fumble_gaps += 1
            else:
                unexplained += 1

    assert checked > 40000, f"only checked {checked} player-weeks"
    assert unexplained == 0, f"{unexplained} player-weeks differ for reasons other than fumbles"
    # The convention gap is real and should show up; if it vanished, the store changed.
    assert fumble_gaps > 0


# --------------------------------------------------------------- the real league


def test_base_league_loads(base: RuleSet) -> None:
    assert base.starter_count == 9
    assert base.scoring["receptions"] == 0.5
    assert base.scoring["fg_made_50_"] == 5
    # Points Allowed 21-27 is toggled off in the league and must stay absent.
    assert "pa_21_27" not in base.scoring


def test_known_player_week(frame: pl.DataFrame, base: RuleSet) -> None:
    """Chris Boswell, 2024 week 1: six field goals of 57, 51, 44, 56, 40, 25."""
    lines = store.load_statlines(SEASON)
    boswell = next(
        line for line in lines.values() if line.name == "Chris Boswell" and line.week == 1
    )
    assert boswell.events["fg_made_yards"] == (57, 51, 44, 56, 40, 25)
    # 57/51/56 at 5, 44/40 at 4, 25 at 3. Pittsburgh won 18-10 entirely on field
    # goals that week, so there are no extra points to add.
    assert boswell.s("pat_made") == 0
    assert score_base(boswell, base) == pytest.approx(5 * 3 + 4 * 2 + 3)


def test_defense_scoring(base: RuleSet) -> None:
    shutout = _line("DEF", pa_0=1, def_sacks=3, def_interceptions=2, def_tds=1)
    assert score_base(shutout, base) == pytest.approx(10 + 3 + 4 + 6)

    blowout = _line("DEF", pa_35_plus=1, def_sacks=1)
    assert score_base(blowout, base) == pytest.approx(-4 + 1)


def test_team_and_player_categories_are_isolated(base: RuleSet) -> None:
    """A team unit's sacks and a quarterback being sacked are different categories."""
    quarterback = _line("QB", def_sacks=5, passing_tds=1)
    assert score_base(quarterback, base) == pytest.approx(4)

    # And the reverse: a team unit earns nothing from player categories.
    unit = _line("DEF", receiving_tds=1, pa_0=1)
    assert score_base(unit, base) == pytest.approx(10)


def test_kickers_score_on_fake_field_goals(base: RuleSet) -> None:
    """A kicker is not confined to kicking categories.

    Yahoo's Offense / Kickers headings organise the settings page; they don't say who
    may earn what. Real occurrences in the store: Chris Boswell threw a touchdown
    pass, Jason Sanders caught one, and Jake Elliott threw an interception -- all on
    fake field goals. Filtering categories by listed position silently dropped every
    one of these.
    """
    passer = _line("K", passing_tds=1, passing_yards=2, fg_made_40_49=1)
    assert score_base(passer, base) == pytest.approx(4 + 0.08 + 4)

    receiver = _line("K", receiving_tds=1, receiving_yards=1, receptions=1)
    assert score_base(receiver, base) == pytest.approx(6 + 0.1 + 0.5)

    picked_off = _line("K", passing_interceptions=1)
    assert score_base(picked_off, base) == pytest.approx(-1)


# --------------------------------------------------------------- validation


def test_unknown_category_is_rejected() -> None:
    with pytest.raises(ValidationError, match="receiving_yards"):
        RuleSet(name="x", lineup={"starters": ["QB"]}, scoring={"recieving_yards": 0.1})


def test_extra_top_level_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RuleSet(name="x", lineup={"starters": ["QB"]}, scorring={})


def test_bonus_only_on_yardage_categories() -> None:
    with pytest.raises(ValidationError, match="bonuses only on"):
        RuleSet(
            name="x",
            lineup={"starters": ["QB"]},
            scoring={"receptions": 1},
            bonuses={"receptions": [{"target": 5, "points": 1}]},
        )


def test_bonus_count_capped() -> None:
    with pytest.raises(ValidationError, match="Yahoo allows 3"):
        RuleSet(
            name="x",
            lineup={"starters": ["QB"]},
            scoring={"rushing_yards": 0.1},
            bonuses={"rushing_yards": [{"target": t, "points": 1} for t in (50, 100, 150, 200)]},
        )


def test_bonus_requires_enabled_category() -> None:
    with pytest.raises(ValidationError, match="isn't enabled"):
        RuleSet(
            name="x",
            lineup={"starters": ["QB"]},
            bonuses={"rushing_yards": [{"target": 100, "points": 1}]},
        )


def test_offense_category_cap() -> None:
    from fantasy_tool.stats import STATS

    offense = [s.key for s in STATS if s.section == "Offense" and s.supported]
    with pytest.raises(ValidationError, match="caps this at"):
        RuleSet(name="x", lineup={"starters": ["QB"]}, scoring=dict.fromkeys(offense, 1.0))


# --------------------------------------------------------------- bonuses & options


def test_bonuses_are_cumulative() -> None:
    rules = RuleSet(
        name="x",
        lineup={"starters": ["RB"]},
        scoring={"rushing_yards": 0.1},
        bonuses={
            "rushing_yards": [
                {"target": 100, "points": 1},
                {"target": 150, "points": 5},
                {"target": 200, "points": 15},
            ]
        },
    )
    # Yahoo's own worked example: 250 rushing yards earns all three bonuses.
    assert score_base(_line("RB", rushing_yards=250), rules) == pytest.approx(25 + 1 + 5 + 15)
    assert score_base(_line("RB", rushing_yards=99), rules) == pytest.approx(9.9)
    # Exactly on the threshold counts.
    assert score_base(_line("RB", rushing_yards=100), rules) == pytest.approx(10 + 1)


def test_fractional_and_negative_switches() -> None:
    def build(**options: bool) -> RuleSet:
        return RuleSet(
            name="x",
            lineup={"starters": ["RB"]},
            options=options,
            scoring={"rushing_yards": 0.1, "rushing_tds": 6},
        )

    line = _line("RB", rushing_yards=54, rushing_tds=1)
    assert score_base(line, build()) == pytest.approx(5.4 + 6)
    assert score_base(line, build(fractional_points=False)) == pytest.approx(5 + 6)

    lost = _line("RB", rushing_yards=-12)
    assert score_base(lost, build()) == pytest.approx(-1.2)
    assert score_base(lost, build(negative_points=False)) == pytest.approx(0.0)
    # Whole points truncate toward zero, so -1.2 becomes -1 rather than -2.
    assert score_base(lost, build(fractional_points=False)) == pytest.approx(-1.0)


# --------------------------------------------------------------- extends


def test_extends_merges_and_overrides(tmp_path: Path) -> None:
    (tmp_path / "parent.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "parent",
                "lineup": {"starters": ["QB", "K"], "bench": 6},
                "scoring": {"passing_tds": 4, "receptions": 0.5, "fg_made_50_": 5},
            }
        )
    )
    (tmp_path / "child.yaml").write_text(
        yaml.safe_dump(
            {
                "extends": "parent.yaml",
                "name": "child",
                "scoring": {"receptions": 1.0, "fg_made_50_": None, "rushing_tds": 6},
            }
        )
    )

    child = load_ruleset(tmp_path / "child.yaml")
    assert child.name == "child"
    assert child.scoring["passing_tds"] == 4  # inherited
    assert child.scoring["receptions"] == 1.0  # overridden
    assert child.scoring["rushing_tds"] == 6  # added
    assert "fg_made_50_" not in child.scoring  # null turns a category off
    assert child.lineup.starters == ["QB", "K"]


def test_extends_is_one_level_only(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(
        yaml.safe_dump({"extends": "b.yaml", "name": "a", "lineup": {"starters": ["QB"]}})
    )
    (tmp_path / "b.yaml").write_text(
        yaml.safe_dump({"extends": "c.yaml", "name": "b", "lineup": {"starters": ["QB"]}})
    )
    with pytest.raises(ValueError, match="only one level"):
        load_ruleset(tmp_path / "a.yaml")
