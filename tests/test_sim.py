"""Verification for the league generator and the season simulator.

Two kinds of check here. Structural invariants -- every slot filled, nobody on two
rosters, points for equalling points against -- catch outright bugs. Calibration
checks catch something subtler and more dangerous: a simulator that runs cleanly but
produces a league that behaves nothing like real fantasy football. The headline metric
this whole tool reports is a rule's swing measured against a typical margin of
victory, so if the margin is wrong every verdict is wrong.
"""

import statistics as st
from itertools import pairwise
from pathlib import Path

import pytest

from fantasy_tool import store
from fantasy_tool.scoring import load_ruleset
from fantasy_tool.sim import simulate, standings_table
from fantasy_tool.sources.synthetic import build_pool, generate

SEASON = 2024
SEEDS = (7, 8, 9, 10, 11)
RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


@pytest.fixture(scope="session")
def rules():
    return load_ruleset(RULES_DIR / "base_yahoo.yaml")


@pytest.fixture(scope="session")
def pool(rules):
    store.sync([SEASON - 1, SEASON])
    return build_pool(SEASON, rules)


@pytest.fixture(scope="session")
def league(pool, rules):
    return generate(pool, seed=7, rules=rules)


@pytest.fixture(scope="session")
def result(league, rules):
    return simulate(league, rules)


@pytest.fixture(scope="session")
def many(pool, rules):
    return [(lg := generate(pool, s, rules), simulate(lg, rules)) for s in SEEDS]


# --------------------------------------------------------------- determinism


def test_same_seed_reproduces_the_league(pool, rules) -> None:
    """The counterfactual depends on this exactly.

    Baseline and candidate are compared by running the same league twice under
    different rules. If the seed didn't reproduce the league, the difference between
    the two runs would include a different draft, and the comparison would be junk.
    """
    first = generate(pool, 42, rules)
    second = generate(pool, 42, rules)
    assert first.rosters == second.rosters
    assert first.lineups == second.lineups
    assert first.schedule == second.schedule
    assert first.meta["skill"] == second.meta["skill"]


def test_different_seeds_give_different_leagues(pool, rules) -> None:
    assert generate(pool, 1, rules).rosters != generate(pool, 2, rules).rosters


def test_simulation_is_deterministic(league, rules) -> None:
    a, b = simulate(league, rules), simulate(league, rules)
    assert a.standings == b.standings


# --------------------------------------------------------------- structure


def test_every_slot_gets_an_eligible_player(league, pool) -> None:
    positions = {p.player_id: p.position for p in pool.players}
    order = sorted(league.settings.slots, key=lambda s: len(s.eligible))
    for lineup in league.lineups.values():
        assert len(lineup) == len(league.settings.slots)
        for slot, player_id in zip(order, lineup, strict=True):
            if player_id:
                assert positions[player_id] in slot.eligible, (slot.label, player_id)


def test_lineups_are_essentially_always_full(league) -> None:
    """A roster should be able to field a legal lineup nearly every week."""
    empty = sum(1 for lineup in league.lineups.values() for p in lineup if not p)
    total = sum(len(lineup) for lineup in league.lineups.values())
    assert empty / total < 0.01, f"{empty}/{total} slots unfilled"


def test_no_player_starts_for_two_teams_in_a_week(league) -> None:
    """Exclusive ownership, including the streamed kickers and defenses."""
    for week in league.settings.weeks:
        started: list[str] = []
        for team in league.settings.teams:
            started.extend(p for p in league.lineups[(team, week)] if p)
        assert len(started) == len(set(started)), f"week {week} has a duplicate starter"


def test_rosters_are_disjoint_and_full_size(league) -> None:
    seen: set[str] = set()
    for team in league.settings.teams:
        roster = league.rosters[team]
        assert len(roster) == league.settings.roster_size
        assert not (roster & seen)
        seen |= roster


def test_every_team_plays_once_a_week(league) -> None:
    for week in league.settings.weeks:
        playing = [t for m in league.week_matchups(week) for t in (m.home, m.away)]
        assert sorted(playing) == sorted(league.settings.teams)


def test_byes_are_derived_correctly(pool) -> None:
    """One bye per NFL team in a modern season."""
    assert len(pool.byes) == 32


# --------------------------------------------------------------- standings


def test_records_account_for_every_game(result, league) -> None:
    weeks = len(league.settings.weeks)
    for record in result.standings.values():
        assert record.games == weeks


def test_wins_and_losses_balance(result) -> None:
    wins = sum(r.wins for r in result.standings.values())
    losses = sum(r.losses for r in result.standings.values())
    ties = sum(r.ties for r in result.standings.values())
    assert wins == losses
    assert ties % 2 == 0


def test_points_for_equals_points_against(result) -> None:
    scored = sum(r.points_for for r in result.standings.values())
    conceded = sum(r.points_against for r in result.standings.values())
    assert scored == pytest.approx(conceded)


def test_standings_are_ordered(result) -> None:
    table = standings_table(result)
    assert table[0][1].win_pct >= table[-1][1].win_pct


# --------------------------------------------------------------- calibration


def _margins(many) -> list[float]:
    return [
        abs(week.team_weeks[m.home].points - week.team_weeks[m.away].points)
        for _, result in many
        for week in result.weeks
        for m in week.matchups
    ]


def test_margin_of_victory_is_realistic(many) -> None:
    """The denominator of the headline metric.

    A rule is judged decisive by comparing its swing against a typical margin. If the
    simulated margin drifted, every verdict would shift with it. Half-PPR leagues run
    around 20-25 points.
    """
    assert 18.0 <= st.median(_margins(many)) <= 28.0


def test_lineup_efficiency_is_realistic(many, pool) -> None:
    """Managers should capture most, not all, of what their roster could score.

    Perfect efficiency would mean hindsight lineups; very low efficiency means the
    projections are noise. Real managers land near 85-90%.
    """
    positions = {p.player_id: p.position for p in pool.players}
    ratios = []
    for league, result in many:
        order = sorted(league.settings.slots, key=lambda s: len(s.eligible))
        for week in result.weeks:
            for team, team_week in week.team_weeks.items():
                remaining, best = set(league.rosters[team]), 0.0
                for slot in order:
                    options = [p for p in remaining if positions.get(p) in slot.eligible]
                    if not options:
                        continue
                    pick = max(options, key=lambda p: pool.points.get((p, week.week), 0.0))
                    remaining.discard(pick)
                    best += pool.points.get((pick, week.week), 0.0)
                if best:
                    ratios.append(team_week.points / best)
    assert 0.80 <= st.median(ratios) <= 0.95


def test_season_spread_is_not_absurd(many) -> None:
    """Best and worst teams should differ, but not by a factor of five."""
    for _, result in many:
        totals = [r.points_for for r in result.standings.values()]
        assert 1.2 <= max(totals) / min(totals) <= 3.0


def test_skill_helps(many) -> None:
    """The whole shark-versus-casual premise: better managers should win more.

    Ground truth exists here only because skill is an input to the generator, which is
    what makes the levelling analysis possible at all.
    """
    pairs = [
        (league.meta["skill"][team], record.win_pct)
        for league, result in many
        for team, record in result.standings.items()
    ]
    top = st.mean(w for s, w in pairs if s >= 0.75)
    bottom = st.mean(w for s, w in pairs if s <= 0.35)
    assert top > bottom + 0.05, f"skilled {top:.3f} vs casual {bottom:.3f}"


# --------------------------------------------------------------- streaming


def test_kickers_and_defenses_are_streamed(league) -> None:
    """They should move, or the position is effectively frozen at the draft."""
    for position_index, label in ((-3, "K"), (-2, "DEF")):
        del position_index, label
    order = sorted(league.settings.slots, key=lambda s: len(s.eligible))
    slot_index = {slot.label: i for i, slot in enumerate(order)}

    changes = 0
    for team in league.settings.teams:
        for label in ("K", "DEF"):
            used = [
                league.lineups[(team, week)][slot_index[label]] for week in league.settings.weeks
            ]
            changes += len(set(used)) - 1
    assert changes > 0, "nobody ever streamed a kicker or defense"


def test_streaming_is_sticky(league) -> None:
    """Managers hold a producing kicker rather than churning every week.

    Free churn would let everyone chase a long-field-goal bonus optimally and
    overstate how exploitable such a rule is.
    """
    order = sorted(league.settings.slots, key=lambda s: len(s.eligible))
    slot_index = {slot.label: i for i, slot in enumerate(order)}
    weeks = league.settings.weeks

    held = []
    for team in league.settings.teams:
        for label in ("K", "DEF"):
            used = [league.lineups[(team, w)][slot_index[label]] for w in weeks]
            same = sum(1 for a, b in pairwise(used) if a == b)
            held.append(same / (len(used) - 1))
    assert st.mean(held) > 0.5, f"kickers and defenses churn too freely: {st.mean(held):.2f}"


# --------------------------------------------------------------- custom rules


def test_custom_rules_reach_the_simulation(pool, league) -> None:
    candidate = load_ruleset(RULES_DIR / "house_2026.yaml")
    result = simulate(league, candidate)

    fired = {
        name: delta
        for week in result.weeks
        for team_week in week.team_weeks.values()
        for line in team_week.scored
        for name, delta in line.rule_points.items()
    }
    assert fired, "no custom rule fired all season"
    assert set(fired) <= set(candidate.custom_rules.enabled)


def test_rules_change_the_totals(league, rules) -> None:
    candidate = load_ruleset(RULES_DIR / "house_2026.yaml")
    base = simulate(league, rules)
    with_rules = simulate(league, candidate)

    base_points = sum(r.points_for for r in base.standings.values())
    candidate_points = sum(r.points_for for r in with_rules.standings.values())
    assert base_points != pytest.approx(candidate_points)


def test_league_is_reproducible_across_processes(pool, rules) -> None:
    """Same seed, same league -- in a *different* interpreter, not just this one.

    Python randomises string hashing per process, so iterating a set of position names
    yields a different order in every run. That reordered the draft board and broke
    ties differently, which meant a seed reproduced a league within one process and
    not between two. Reported numbers have to reproduce tomorrow, so this runs a
    subprocess with a hash seed that differs from ours and compares.
    """
    import hashlib
    import json
    import os
    import subprocess
    import sys

    def fingerprint(league) -> str:
        blob = json.dumps({t: sorted(v) for t, v in sorted(league.rosters.items())}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    script = (
        "import hashlib, json;"
        "from fantasy_tool.scoring import load_ruleset;"
        "from fantasy_tool.sources.synthetic import build_pool, generate;"
        f"r = load_ruleset({str(RULES_DIR / 'base_yahoo.yaml')!r});"
        f"lg = generate(build_pool({SEASON}, r), 7, r);"
        "blob = json.dumps({t: sorted(v) for t, v in sorted(lg.rosters.items())}, sort_keys=True);"
        "print(hashlib.sha256(blob.encode()).hexdigest())"
    )
    environment = {**os.environ, "PYTHONHASHSEED": "12345"}
    other = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
        cwd=str(RULES_DIR.parent),
    )
    assert other.stdout.strip() == fingerprint(generate(pool, 7, rules))
