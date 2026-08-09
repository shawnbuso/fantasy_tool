"""Verification for the parameter sweep."""

from pathlib import Path

import pytest

from fantasy_tool import store
from fantasy_tool import sweep as sweep_module
from fantasy_tool.harness import build_leagues, run_pairs
from fantasy_tool.scoring import RuleSet, load_ruleset

SEASON = 2024
RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


@pytest.fixture(scope="session")
def baseline() -> RuleSet:
    return load_ruleset(RULES_DIR / "base_yahoo.yaml")


@pytest.fixture(scope="session")
def candidate() -> RuleSet:
    return load_ruleset(RULES_DIR / "house_2026.yaml")


@pytest.fixture(scope="session")
def leagues(baseline):
    store.sync([SEASON - 1, SEASON])
    return build_leagues([SEASON], baseline, leagues=3, seed=7)


# --------------------------------------------------------------- parsing


def test_parse_spec() -> None:
    assert sweep_module.parse_spec("blowout.penalty=-6,-12,-40") == (
        "blowout",
        "penalty",
        [-6, -12, -40],
    )


def test_parse_spec_coerces_types() -> None:
    _, _, values = sweep_module.parse_spec("r.p=1,2.5,true,auto")
    assert values == [1, 2.5, True, "auto"]


@pytest.mark.parametrize("bad", ["no_equals", "missing.values=", "noparam=1,2"])
def test_parse_spec_rejects_nonsense(bad: str) -> None:
    with pytest.raises(ValueError):
        sweep_module.parse_spec(bad)


def test_apply_settings_overrides_one_parameter(candidate) -> None:
    tuned = sweep_module.apply_settings(candidate, (("def_blowout_penalty", "penalty", -12),))
    assert tuned.custom_rules.enabled["def_blowout_penalty"]["penalty"] == -12
    # Other rules and other parameters are untouched.
    assert tuned.custom_rules.enabled["def_blowout_penalty"]["threshold"] == 40
    assert tuned.custom_rules.enabled["fg_long_bonus"]["bonus"] == 50
    # And the original is unchanged.
    assert candidate.custom_rules.enabled["def_blowout_penalty"]["penalty"] == -40


def test_apply_settings_rejects_a_rule_that_is_not_enabled(candidate) -> None:
    with pytest.raises(ValueError, match="not enabled"):
        sweep_module.apply_settings(candidate, (("no_such_rule", "x", 1),))


# --------------------------------------------------------------- sweeping


@pytest.fixture(scope="session")
def penalty_sweep(leagues, baseline, candidate):
    return sweep_module.run(
        leagues, baseline, candidate, ["def_blowout_penalty.penalty=-2,-10,-25,-60"]
    )


def test_sweep_covers_every_value(penalty_sweep) -> None:
    assert len(penalty_sweep) == 4
    assert [p.label for p in penalty_sweep] == [
        "penalty=-2",
        "penalty=-10",
        "penalty=-25",
        "penalty=-60",
    ]


def test_bigger_penalties_hit_harder(penalty_sweep) -> None:
    """The sanity check that the sweep is measuring anything at all."""
    magnitudes = [p.impact.mean_when_fired for p in penalty_sweep]
    assert magnitudes == sorted(magnitudes)


def test_flip_rate_rises_with_magnitude(penalty_sweep) -> None:
    """More extreme settings must change more outcomes, or the sweep is noise."""
    flips = [p.impact.flips.rate for p in penalty_sweep]
    assert flips[-1] > flips[0]
    assert sweep_module.is_monotonic(penalty_sweep)


def test_trigger_rate_is_unchanged_by_magnitude(penalty_sweep) -> None:
    """Changing what a trigger is worth doesn't change how often it fires."""
    rates = {round(p.impact.fired.rate, 6) for p in penalty_sweep}
    assert len(rates) == 1


def test_threshold_changes_how_often_it_fires(leagues, baseline, candidate) -> None:
    """The other lever: a higher bar for a blowout should fire less often."""
    points = sweep_module.run(
        leagues, baseline, candidate, ["def_blowout_penalty.threshold=28,35,45"]
    )
    rates = [p.impact.fired.rate for p in points]
    assert rates[0] > rates[-1]


def test_grid_sweep_covers_the_product(leagues, baseline, candidate) -> None:
    points = sweep_module.run(
        leagues,
        baseline,
        candidate,
        ["fg_long_bonus.bonus=5,25", "fg_long_bonus.min_yards=50,55"],
    )
    assert len(points) == 4
    assert points[0].label == "bonus=5, min_yards=50"


# --------------------------------------------------------------- recommending


def test_recommends_the_strongest_acceptable_setting(penalty_sweep) -> None:
    best = sweep_module.recommend(penalty_sweep)
    if best is None:
        pytest.skip("every setting decided games")
    assert best.impact.verdict not in ("AUTO-DECIDE", "LOTTERY TICKET")
    # Nothing stronger should also have been acceptable.
    stronger = [
        p
        for p in penalty_sweep
        if p.impact.mean_when_fired > best.impact.mean_when_fired
        and p.impact.verdict not in ("AUTO-DECIDE", "LOTTERY TICKET")
    ]
    assert not stronger


def test_recommends_nothing_when_every_setting_decides_games(leagues, baseline, candidate) -> None:
    """Returning nothing is the answer: the rule needs rethinking, not tuning."""
    points = sweep_module.run(leagues, baseline, candidate, ["fg_long_bonus.bonus=400,800,1600"])
    assert all(p.impact.verdict == "AUTO-DECIDE" for p in points)
    assert sweep_module.recommend(points) is None


def test_a_zero_setting_is_inert(leagues, baseline, candidate) -> None:
    """Sweeping down to nothing should measure as nothing -- the null rule, via config."""
    points = sweep_module.run(leagues, baseline, candidate, ["fg_long_bonus.bonus=0"])
    fired = {
        name
        for point in points
        for impact in point.analysis.per_rule
        if impact.fired.successes
        for name in [impact.name]
    }
    assert "fg_long_bonus" not in fired


def test_monotonicity_check_needs_enough_points(penalty_sweep) -> None:
    assert sweep_module.is_monotonic(penalty_sweep[:2])  # too short to judge


def test_monotonicity_is_not_claimed_for_a_grid(leagues, baseline, candidate) -> None:
    """A grid varies along two axes, so flattening it is non-monotonic by construction.

    Warning about that would be a false alarm every time someone sweeps two parameters.
    """
    points = sweep_module.run(
        leagues,
        baseline,
        candidate,
        ["fg_long_bonus.bonus=3,12", "fg_long_bonus.min_yards=50,55,60"],
    )
    assert len(points) == 6
    assert sweep_module.is_monotonic(points)


# --------------------------------------------------------------- harness


def test_baseline_is_shared_across_settings(leagues, baseline, candidate) -> None:
    """Every setting must be measured against an identical baseline."""
    from fantasy_tool.sim import simulate

    shared = [simulate(lg, baseline) for lg in leagues]
    first = run_pairs(leagues, baseline, candidate, baseline_results=shared)
    second = run_pairs(leagues, baseline, candidate, baseline_results=shared)
    assert [b.standings for b, _ in first] == [b.standings for b, _ in second]


def test_leagues_are_reused_not_regenerated(leagues, baseline) -> None:
    again = build_leagues([SEASON], baseline, leagues=3, seed=7)
    assert [lg.key for lg in again] == [lg.key for lg in leagues]
    assert [lg.rosters for lg in again] == [lg.rosters for lg in leagues]
