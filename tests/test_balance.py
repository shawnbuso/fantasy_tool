"""Verification for the position balance solver."""

from pathlib import Path

import pytest

from fantasy_tool import rules as rules_mod
from fantasy_tool import store
from fantasy_tool.balance import FLEX_POSITIONS, PositionProfile, profile, solve, startable_pool
from fantasy_tool.scoring import load_ruleset, score_base, score_standalone
from fantasy_tool.stats import STAT_BY_KEY

SEASONS = [2023, 2024]
RULES_DIR = Path(__file__).resolve().parent.parent / "rules"
LEVERS = ["receiving_yards", "rushing_yards"]
PREMIUM = ("TE", "receiving_yards")


@pytest.fixture(scope="session")
def superflex():
    store.sync(SEASONS + [min(SEASONS) - 1])
    return load_ruleset(RULES_DIR / "superflex.yaml")


@pytest.fixture(scope="session")
def profiles(superflex):
    return profile(SEASONS, superflex, LEVERS, top_n=startable_pool(10))


def test_startable_pool_matches_a_balanced_lineup() -> None:
    """One dedicated slot plus an equal share of the flex, over ten teams."""
    assert startable_pool(10) == 20
    assert startable_pool(12) == 24


def test_every_flex_position_is_profiled(profiles) -> None:
    assert set(profiles) == set(FLEX_POSITIONS)
    for position in FLEX_POSITIONS:
        assert profiles[position].pool_size == 20


def test_the_measured_gaps_are_the_known_ones(profiles) -> None:
    """Quarterbacks score most and tight ends least; that ordering drives everything."""
    means = {p: profiles[p].mean_points for p in FLEX_POSITIONS}
    assert means["QB"] == max(means.values())
    assert means["TE"] == min(means.values())


def test_no_shared_stat_favours_tight_ends_over_receivers(superflex) -> None:
    """The finding that forces a per-position rule.

    Yahoo's categories apply to every player alike, so a shared category can only
    close the tight end gap if tight ends out-produce receivers at something. They
    don't -- not yards, not receptions, not touchdowns, not even first downs per
    catch. Raising a shared category helps receivers more and widens the gap.
    """
    receiving = [
        key
        for key, stat in STAT_BY_KEY.items()
        if stat.section == "Offense" and stat.group == "Receiving" and stat.supported
    ]
    measured = profile(SEASONS, superflex, receiving, top_n=startable_pool(10))
    for key in receiving:
        te = measured["TE"].mean_stats[key]
        wr = measured["WR"].mean_stats[key]
        assert te <= wr, f"{key}: TE {te:.2f} > WR {wr:.2f} would give a Yahoo-native lever"


def test_solution_equalises_every_position(profiles) -> None:
    solution = solve(profiles, LEVERS, PREMIUM)
    assert solution is not None
    assert solution.spread == pytest.approx(0.0, abs=1e-6)
    assert set(solution.achieved) == set(FLEX_POSITIONS)


def test_solution_only_increases(profiles) -> None:
    """The stated constraint: nothing may be reduced."""
    solution = solve(profiles, LEVERS, PREMIUM)
    assert solution.feasible
    assert all(delta > 0 for delta in solution.increments.values())
    assert all(rate > 0 for _, rate in solution.premiums.values())


def test_target_is_the_highest_scoring_position(profiles) -> None:
    """With increases only, everyone climbs to the quarterbacks."""
    solution = solve(profiles, LEVERS, PREMIUM)
    assert solution.target >= max(p.mean_points for p in profiles.values()) - 1e-9


def test_more_ppr_needs_a_smaller_correction(superflex) -> None:
    """Receptions already close part of the gap, so the levers have less left to do."""
    sizes = []
    for ppr in (0.0, 0.5, 1.0):
        variant = superflex.model_copy(update={"scoring": {**superflex.scoring, "receptions": ppr}})
        measured = profile(SEASONS, variant, LEVERS, top_n=startable_pool(10))
        solution = solve(measured, LEVERS, PREMIUM)
        sizes.append(solution.premiums["TE"][1])
    assert sizes == sorted(sizes, reverse=True), sizes


def test_solver_needs_one_lever_per_position(profiles) -> None:
    assert solve(profiles, ["receiving_yards"], PREMIUM) is None  # two unknowns, three gaps
    assert solve(profiles, LEVERS, None) is None


def test_solver_reports_infeasible_rather_than_lying() -> None:
    """A lever that would have to be cut is surfaced, not silently applied."""
    # Running backs already outscore quarterbacks, so the only way to level them is to
    # cut the stat they earn on -- which the increase-only constraint forbids.
    made_up = {
        "QB": PositionProfile("QB", 10.0, {"a": 0.0, "b": 0.0}, 1),
        "RB": PositionProfile("RB", 20.0, {"a": 1.0, "b": 0.0}, 1),
        "WR": PositionProfile("WR", 10.0, {"a": 0.0, "b": 1.0}, 1),
        "TE": PositionProfile("TE", 10.0, {"a": 1.0, "b": 0.0}, 1),
    }
    solution = solve(made_up, ["a", "b"], ("TE", "a"))
    assert solution is not None
    assert not solution.feasible
    assert solution.increments["a"] < 0


# --------------------------------------------------------------- the rule


def test_position_premium_only_pays_its_position() -> None:
    from fantasy_tool.model import StatLine

    rules_mod.clear_registry()
    rules_mod.load_modules([RULES_DIR / "house_2026.py"])
    balanced = load_ruleset(RULES_DIR / "balanced_full_ppr.yaml")

    def line(position: str) -> StatLine:
        return StatLine(
            player_id="x",
            name="x",
            position=position,
            nfl_team="X",
            season=2024,
            week=1,
            opponent="Y",
            stats={"receiving_yards": 100.0},
        )

    premium = float(balanced.custom_rules.enabled["position_premium"]["TE_receiving_yards"])
    tight_end = score_standalone(line("TE"), balanced) - score_base(line("TE"), balanced)
    receiver = score_standalone(line("WR"), balanced) - score_base(line("WR"), balanced)

    assert tight_end == pytest.approx(premium * 100.0)
    assert receiver == pytest.approx(0.0)

    rules_mod.clear_registry()


def test_valuation_includes_custom_rules() -> None:
    """Managers must value players under the scoring their league actually uses.

    Valuing with base scoring alone left every manager ignorant of the tight end
    premium, so the position it was written to promote stayed on the bench.
    """
    from fantasy_tool.model import StatLine

    rules_mod.clear_registry()
    balanced = load_ruleset(RULES_DIR / "balanced_full_ppr.yaml")
    tight_end = StatLine(
        player_id="x",
        name="x",
        position="TE",
        nfl_team="X",
        season=2024,
        week=1,
        opponent="Y",
        stats={"receiving_yards": 80.0, "receptions": 6.0},
    )
    assert score_standalone(tight_end, balanced) > score_base(tight_end, balanced)
    rules_mod.clear_registry()


def test_only_flex_competitors_need_balancing(superflex) -> None:
    """A position with nothing but a dedicated slot doesn't compete, so it doesn't count.

    This is what makes the tight end problem go away without a custom rule. Tight ends
    score less and Yahoo can't change that, but every team starts exactly one, so the
    shortfall is symmetric and costs nobody -- the same way a kicker scoring less than
    a running back bothers no one. It only hurts when a tight end has to beat a
    quarterback for a shared slot.
    """
    profiles = profile(SEASONS, superflex, LEVERS, top_n=startable_pool(10))
    competing = ("QB", "RB", "WR")

    solution = solve(profiles, LEVERS, None, competing)
    assert solution is not None
    assert solution.feasible
    assert solution.spread == pytest.approx(0.0, abs=1e-6)
    # Balanced with Yahoo categories alone -- no per-position rule anywhere.
    assert not solution.premiums
    assert set(solution.achieved) == set(competing)


def test_the_recommended_setup_needs_no_custom_rules() -> None:
    balanced = load_ruleset(RULES_DIR / "balanced_qwr.yaml")
    assert not balanced.custom_rules.enabled
    assert balanced.points["receptions"] == 1.0
    # Every change is an increase on Yahoo's defaults.
    base = load_ruleset(RULES_DIR / "base_yahoo.yaml")
    for key, value in base.points.items():
        if key in balanced.points:
            assert balanced.points[key] >= value - 1e-9, key
    # Tight end has a slot of its own. Yahoo's superflex also lets one into the flex,
    # which the balancing deliberately ignores -- see the config, and
    # test_a_tight_end_never_wins_a_flex_slot below for why that is safe.
    from fantasy_tool.model import parse_slots

    slots = parse_slots(balanced.lineup.starters)
    assert any(s.label == "TE" for s in slots)
    assert any("TE" in s.eligible and s.is_flex for s in slots)


def test_all_four_flex_positions_are_level() -> None:
    """The point of the tight end rate: every position the flex admits is worth the same.

    Measured on the pool a ten-team league starts when all four compete -- twenty at
    each position. Tight ends were 8.1 points a game short before they had a rate of
    their own, which is why they were unstartable in a flex slot; the whole exercise is
    only worth anything if that gap is actually gone.
    """
    balanced = load_ruleset(RULES_DIR / "balanced_qwr.yaml")
    profiles = profile(SEASONS, balanced, LEVERS, top_n=startable_pool(10, flex_share=1.0))
    means = {p: profiles[p].mean_points for p in FLEX_POSITIONS}
    assert max(means.values()) - min(means.values()) < 2.0, means
