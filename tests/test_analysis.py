"""Verification for the counterfactual comparison.

The bracket tests are the most important in the project. A rule that does nothing must
measure as exactly nothing, a rule that helps both sides equally must flip no games,
and a rule big enough to decide everything must measure as decisive. If any of those
is off, every number the tool reports is suspect -- and unlike a crash, a contaminated
counterfactual produces perfectly plausible output.
"""

import statistics as st
from pathlib import Path

import pytest

from fantasy_tool import rules as rules_mod
from fantasy_tool import store
from fantasy_tool.analysis import Rate, compare, diff_season, spearman
from fantasy_tool.rules import RuleContext, rule
from fantasy_tool.scoring import RuleSet, load_ruleset
from fantasy_tool.sim import simulate
from fantasy_tool.sources.synthetic import build_pool, generate

SEASON = 2024
SEEDS = (7, 8, 9)
RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


@pytest.fixture(scope="session")
def baseline() -> RuleSet:
    return load_ruleset(RULES_DIR / "base_yahoo.yaml")


@pytest.fixture(scope="session")
def pool(baseline):
    store.sync([SEASON - 1, SEASON])
    return build_pool(SEASON, baseline)


@pytest.fixture(scope="session")
def leagues(pool, baseline):
    return [generate(pool, seed, baseline) for seed in SEEDS]


def _with_rules(baseline: RuleSet, enabled: dict) -> RuleSet:
    return baseline.model_copy(
        update={"custom_rules": baseline.custom_rules.model_copy(update={"enabled": enabled})}
    )


def _run(leagues, baseline: RuleSet, candidate: RuleSet):
    return [(simulate(lg, baseline), simulate(lg, candidate)) for lg in leagues]


# ------------------------------------------------------- the bracket tests


def test_null_rule_changes_nothing_at_all(leagues, baseline) -> None:
    """A rule returning zero must be indistinguishable from no rule.

    This is the strictest check available: not "close", exactly equal. Any drift here
    means something other than the rule differs between the two runs, and every
    downstream number is contaminated.
    """

    @rule("nothing_at_all")
    def _nothing(ctx: RuleContext) -> float:
        return 0.0

    candidate = _with_rules(baseline, {"nothing_at_all": {}})
    pairs = _run(leagues, baseline, candidate)
    analysis = compare(pairs, baseline, candidate, [lg.meta["skill"] for lg in leagues])

    for base, cand in pairs:
        assert base.standings == cand.standings
    for diff in (d for pair in pairs for d in diff_season(*pair)):
        assert diff.swing == 0.0
        assert not diff.triggered
        assert not diff.flipped

    assert analysis.overall.flips.successes == 0
    assert analysis.overall.fired.successes == 0
    assert analysis.balance.wins_stdev_base == analysis.balance.wins_stdev_candidate
    assert analysis.luck.points_to_wins_base == analysis.luck.points_to_wins_candidate

    rules_mod.clear_registry()


def test_constant_rule_pays_every_starter_equally(leagues, baseline) -> None:
    """A rule paying every starter a flat amount, and what that does and doesn't do.

    It catches asymmetry bugs a null rule can't -- crediting the home team twice, or
    applying a rule to one side of a matchup only.

    It is *not* quite neutral, and the reason is worth keeping: a per-starter bonus
    pays a full lineup more than one with an empty slot, so the rare matchup between
    unequally-filled lineups does move. Anywhere both teams fielded the same number of
    starters, the margin must be untouched to the last decimal.
    """

    @rule("five_for_everyone")
    def _five(ctx: RuleContext) -> float:
        return 5.0

    candidate = _with_rules(baseline, {"five_for_everyone": {}})
    pairs = _run(leagues, baseline, candidate)
    analysis = compare(pairs, baseline, candidate)

    filled_by: dict[tuple[str, str, int], int] = {}
    for league, (base, cand) in zip(leagues, pairs, strict=True):
        for team in base.settings.teams:
            total = 0
            for week in league.settings.weeks:
                count = sum(1 for player_id in league.lineups[(team, week)] if player_id)
                filled_by[(league.key, team, week)] = count
                total += count
            gained = cand.standings[team].points_for - base.standings[team].points_for
            assert gained == pytest.approx(5.0 * total)

    equal, moved = 0, 0
    for diff in (d for pair in pairs for d in diff_season(*pair)):
        home = filled_by[(diff.league_key, diff.home, diff.week)]
        away = filled_by[(diff.league_key, diff.away, diff.week)]
        if home == away:
            equal += 1
            assert diff.swing == pytest.approx(0.0, abs=1e-9)
            assert not diff.flipped
        elif diff.swing:
            moved += 1
            assert abs(diff.swing) == pytest.approx(5.0 * abs(home - away))

    assert equal > 0.9 * (equal + moved), "lineups should almost always be full"
    assert analysis.overall.fired.rate == 1.0

    rules_mod.clear_registry()


def test_absurd_rule_is_measured_as_decisive(leagues, baseline) -> None:
    """A rule worth a thousand points a field goal must read as deciding everything.

    Note it has to scale with something that differs between the two teams. A flat
    thousand for every kicker pays both sides equally and cancels -- that is a
    constant rule wearing a large number, and correctly measures as no swing at all.
    """

    @rule("thousand_per_kick", positions=["K"])
    def _thousand(ctx: RuleContext) -> float:
        return 1000.0 * ctx.line.s("fg_made")

    candidate = _with_rules(baseline, {"thousand_per_kick": {}})
    pairs = _run(leagues, baseline, candidate)
    analysis = compare(pairs, baseline, candidate)

    impact = analysis.overall
    assert impact.decisive.rate > 0.95
    assert impact.verdict == "AUTO-DECIDE"
    assert impact.median_swing > 10 * analysis.median_margin
    # Whenever the kickers differ it decides the game, so it flips roughly every
    # matchup the eventual loser was winning -- far above zero.
    assert impact.flips.rate > 0.2

    rules_mod.clear_registry()


def test_a_big_but_symmetric_rule_flips_nothing(leagues, baseline) -> None:
    """Size alone doesn't make a rule decisive; asymmetry does.

    A thousand points to every team's kicker moves every score enormously and changes
    no result. Reporting that as decisive would be a serious false positive, since it
    is the shape a badly-designed participation bonus takes.
    """

    @rule("thousand_flat", positions=["K"])
    def _flat(ctx: RuleContext) -> float:
        return 1000.0

    candidate = _with_rules(baseline, {"thousand_flat": {}})
    analysis = compare(_run(leagues, baseline, candidate), baseline, candidate)

    assert analysis.overall.flips.successes == 0
    assert analysis.overall.decisive.successes == 0
    assert analysis.overall.verdict == "FLAVOR"

    rules_mod.clear_registry()


# ------------------------------------------------------- real candidate rules


def test_the_stated_house_rules(leagues, baseline) -> None:
    """The two rules that prompted this project should read as clearly too swingy."""
    candidate = load_ruleset(RULES_DIR / "house_2026.yaml")
    pairs = _run(leagues, baseline, candidate)
    analysis = compare(pairs, baseline, candidate, [lg.meta["skill"] for lg in leagues])

    by_name = {impact.name: impact for impact in analysis.per_rule}
    assert set(by_name) == {"fg_long_bonus", "def_blowout_penalty"}

    long_fg = by_name["fg_long_bonus"]
    assert long_fg.median_swing > analysis.median_margin
    assert long_fg.verdict in ("AUTO-DECIDE", "HIGH SWING", "LOTTERY TICKET")

    blowout = by_name["def_blowout_penalty"]
    assert blowout.fired.rate < long_fg.fired.rate  # far rarer
    assert blowout.mean_when_fired == pytest.approx(40.0)

    rules_mod.clear_registry()


def test_a_gentle_rule_reads_as_flavor(leagues, baseline) -> None:
    """The verdict scale has to distinguish, or it says AUTO-DECIDE about everything."""

    @rule("tiny_bonus", positions=["TE"])
    def _tiny(ctx: RuleContext) -> float:
        return 0.5

    candidate = _with_rules(baseline, {"tiny_bonus": {}})
    analysis = compare(_run(leagues, baseline, candidate), baseline, candidate)
    assert analysis.overall.verdict == "FLAVOR"

    rules_mod.clear_registry()


def test_lottery_ticket_is_flagged(leagues, baseline) -> None:
    """Rare but enormous is the worst failure mode, and invisible to a flip rate."""

    @rule("jackpot", positions=["K"])
    def _jackpot(ctx: RuleContext) -> float:
        return 500.0 if ctx.line.s("fg_made") >= 5 else 0.0

    candidate = _with_rules(baseline, {"jackpot": {}})
    analysis = compare(_run(leagues, baseline, candidate), baseline, candidate)
    impact = analysis.overall
    assert impact.fired.rate < 0.02
    assert impact.verdict == "LOTTERY TICKET"

    rules_mod.clear_registry()


# ------------------------------------------------------- supporting maths


def test_wilson_interval_brackets_the_rate() -> None:
    rate = Rate(50, 100)
    low, high = rate.interval
    assert low < rate.rate < high
    # A larger sample must give a tighter interval.
    wide = Rate(5, 10).interval
    narrow = Rate(500, 1000).interval
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_wilson_handles_the_edges() -> None:
    assert Rate(0, 0).interval == (0.0, 0.0)
    assert Rate(0, 50).interval[0] == 0.0
    assert Rate(50, 50).interval[1] == 1.0


def test_spearman_basics() -> None:
    assert spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    # Monotone but non-linear still reads as perfect rank correlation.
    assert spearman([1, 2, 3, 4], [1, 4, 9, 16]) == pytest.approx(1.0)
    assert spearman([1], [1]) == 0.0


def test_ties_are_not_counted_as_wins() -> None:
    """Flip detection needs three states, not two."""
    from fantasy_tool.analysis import MatchupDiff

    drawn = MatchupDiff("k", 1, "a", "b", 100.0, 100.0, 100.0, 100.0)
    assert not drawn.flipped
    broken = MatchupDiff("k", 1, "a", "b", 100.0, 100.0, 101.0, 100.0, {"r": 1.0})
    assert broken.flipped


def test_mismatched_leagues_are_refused(leagues, baseline) -> None:
    """Joining the wrong pairs would silently produce meaningless numbers."""
    first = simulate(leagues[0], baseline)
    second = simulate(leagues[1], baseline)
    with pytest.raises(ValueError, match="different leagues"):
        diff_season(first, second)


def test_lineup_change_is_flagged_as_incomparable(leagues, baseline) -> None:
    """Changing roster slots means lineups differ by construction, not by rule."""
    superflex = baseline.model_copy(
        update={"lineup": baseline.lineup.model_copy(update={"starters": ["QB", "Q/W/R/T"]})}
    )
    analysis = compare(_run(leagues, baseline, baseline), baseline, superflex)
    assert not analysis.comparable_lineups


def test_all_play_correlation_is_sane(leagues, baseline) -> None:
    """With no rule, all-play correlation must be identical in both runs."""
    analysis = compare(_run(leagues, baseline, baseline), baseline, baseline)
    assert analysis.luck.allplay_to_actual_base == analysis.luck.allplay_to_actual_candidate
    assert 0.0 < analysis.luck.allplay_to_actual_base <= 1.0


def test_margin_matches_the_simulation(leagues, baseline) -> None:
    pairs = _run(leagues, baseline, baseline)
    analysis = compare(pairs, baseline, baseline)
    margins = [abs(d.margin_base) for pair in pairs for d in diff_season(*pair)]
    assert analysis.median_margin == pytest.approx(st.median(margins))
