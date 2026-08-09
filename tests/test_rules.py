"""Verification for custom Python rules.

Threshold rules are argued about at their edges -- is exactly 40 points allowed a
blowout, does a second long kick pay twice -- so that is what these pin down. Getting
an edge wrong shifts how often a rule fires, which is the number the whole tool exists
to report.
"""

from pathlib import Path

import pytest

from fantasy_tool import rules as rules_mod
from fantasy_tool.model import History, Matchup, ScoredLine, StatLine, TeamWeek, WeekResult
from fantasy_tool.rules import RuleContext, evaluate, rule
from fantasy_tool.scoring import load_ruleset

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test gets a clean registry; the decorator writes to module-level state."""
    rules_mod.clear_registry()
    yield
    rules_mod.clear_registry()


@pytest.fixture
def house() -> None:
    rules_mod.load_modules([RULES_DIR / "house_2026.py"])


def _line(position: str, player_id: str = "p1", week: int = 5, **stats: float) -> StatLine:
    return StatLine(
        player_id=player_id,
        name=player_id,
        position=position,
        nfl_team="X",
        season=2024,
        week=week,
        opponent="Y",
        stats=stats,
    )


def _team(name: str, *scored: ScoredLine, week: int = 5) -> TeamWeek:
    return TeamWeek(team=name, week=week, scored=tuple(scored))


def _context(
    line: StatLine, base: float = 0.0, *, params=None, team=None, opponent=None, history=None
) -> RuleContext:
    return RuleContext(
        line=line,
        base=base,
        params=params or {},
        team=team or _team("mine", ScoredLine(line, base)),
        opponent=opponent or _team("theirs"),
        history=history or History(),
    )


def _history(*week_specs: dict[str, float]) -> History:
    """Build a history from {team: points} per week, two teams playing each other."""
    weeks = []
    for index, points in enumerate(week_specs, start=1):
        names = list(points)
        team_weeks = {
            name: _team(name, ScoredLine(_line("RB", week=index), points[name]), week=index)
            for name in names
        }
        weeks.append(WeekResult(index, team_weeks, (Matchup(index, names[0], names[1]),)))
    return History(tuple(weeks))


# --------------------------------------------------------------- registry


def test_rule_registers_with_positions() -> None:
    @rule("only_kickers", positions=["K"])
    def _only_kickers(ctx: RuleContext) -> float:
        return 1.0

    registered = rules_mod.registered()
    assert registered["only_kickers"].positions == frozenset({"K"})


def test_rule_defaults_to_every_position() -> None:
    @rule("everyone")
    def _everyone(ctx: RuleContext) -> float:
        return 1.0

    assert "DEF" in rules_mod.registered()["everyone"].positions


def test_unknown_position_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown position"):

        @rule("bad", positions=["QB", "PUNTER"])
        def _bad(ctx: RuleContext) -> float:
            return 0.0


def test_duplicate_name_is_rejected() -> None:
    @rule("clash")
    def _first(ctx: RuleContext) -> float:
        return 1.0

    with pytest.raises(ValueError, match="both named"):

        @rule("clash")
        def _second(ctx: RuleContext) -> float:
            return 2.0


def test_missing_module_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError):
        rules_mod.load_modules([RULES_DIR / "nope.py"])


def test_loading_the_same_module_twice_is_harmless() -> None:
    """A single run loads a rule file repeatedly and must not trip over itself.

    Comparing a baseline against a candidate loads both rule sets, and a sweep loads
    one many times over. Re-executing the module would create fresh function objects
    and fire the duplicate-name guard, which exists for two genuinely different rules
    claiming one name.
    """
    rules_mod.load_modules([RULES_DIR / "house_2026.py"])
    before = rules_mod.registered()
    rules_mod.load_modules([RULES_DIR / "house_2026.py"])
    assert rules_mod.registered() == before


def test_loading_the_same_ruleset_twice_is_harmless() -> None:
    first = load_ruleset(RULES_DIR / "house_2026.yaml")
    second = load_ruleset(RULES_DIR / "house_2026.yaml")
    assert first.custom_rules.enabled == second.custom_rules.enabled


# --------------------------------------------------------------- evaluate


def test_evaluate_filters_by_position() -> None:
    @rule("kickers_only", positions=["K"])
    def _kickers_only(ctx: RuleContext) -> float:
        return 7.0

    assert evaluate(_context(_line("K")), {"kickers_only": {}}) == {"kickers_only": 7.0}
    assert evaluate(_context(_line("QB")), {"kickers_only": {}}) == {}


def test_evaluate_omits_rules_that_did_not_fire() -> None:
    @rule("quiet")
    def _quiet(ctx: RuleContext) -> float:
        return 0.0

    assert evaluate(_context(_line("QB")), {"quiet": {}}) == {}


def test_evaluate_passes_params_per_rule() -> None:
    @rule("echo")
    def _echo(ctx: RuleContext) -> float:
        return float(ctx.param("amount", 1.0))

    assert evaluate(_context(_line("QB")), {"echo": {"amount": 12}}) == {"echo": 12.0}


def test_enabled_but_unregistered_is_an_error() -> None:
    with pytest.raises(KeyError, match="not registered"):
        evaluate(_context(_line("QB")), {"ghost": {}})


def test_scored_line_total_is_base_plus_rules() -> None:
    scored = ScoredLine(_line("QB"), base=12.5, rule_points={"a": 3.0, "b": -1.5})
    assert scored.total == pytest.approx(14.0)


# --------------------------------------------------------------- fg_long_bonus


def _kicker(*made: int) -> StatLine:
    return StatLine(
        player_id="k",
        name="k",
        position="K",
        nfl_team="X",
        season=2024,
        week=5,
        opponent="Y",
        stats={"fg_made": float(len(made))},
        events={"fg_made_yards": made},
    )


def test_long_fg_boundary(house: None) -> None:
    """Exactly 50 counts; 49 does not."""
    enabled = {"fg_long_bonus": {"min_yards": 50, "bonus": 50}}
    assert evaluate(_context(_kicker(50)), enabled) == {"fg_long_bonus": 50.0}
    assert evaluate(_context(_kicker(49)), enabled) == {}


def test_long_fg_pays_per_kick(house: None) -> None:
    """Two long kicks in a week pay twice -- worth knowing before adopting it."""
    enabled = {"fg_long_bonus": {"min_yards": 50, "bonus": 50}}
    assert evaluate(_context(_kicker(57, 51, 44)), enabled) == {"fg_long_bonus": 100.0}


def test_long_fg_cap(house: None) -> None:
    enabled = {"fg_long_bonus": {"min_yards": 50, "bonus": 50, "cap": 60}}
    assert evaluate(_context(_kicker(57, 51)), enabled) == {"fg_long_bonus": 60.0}


def test_long_fg_threshold_is_tunable(house: None) -> None:
    """The point of reading exact yardages: any threshold, no new stat columns."""
    enabled = {"fg_long_bonus": {"min_yards": 55, "bonus": 10}}
    assert evaluate(_context(_kicker(57, 51)), enabled) == {"fg_long_bonus": 10.0}


def test_kicker_with_no_kicks(house: None) -> None:
    assert evaluate(_context(_kicker()), {"fg_long_bonus": {}}) == {}


# --------------------------------------------------------------- def_blowout_penalty


def test_blowout_boundary(house: None) -> None:
    """Exactly 40 allowed triggers; 39 does not."""
    enabled = {"def_blowout_penalty": {"threshold": 40, "penalty": -40}}
    at = _line("DEF", player_id="SF", def_points_allowed=40)
    below = _line("DEF", player_id="SF", def_points_allowed=39)
    assert evaluate(_context(at), enabled) == {"def_blowout_penalty": -40.0}
    assert evaluate(_context(below), enabled) == {}


def test_blowout_only_applies_to_defenses(house: None) -> None:
    quarterback = _line("QB", def_points_allowed=45)
    assert evaluate(_context(quarterback), {"def_blowout_penalty": {}}) == {}


# --------------------------------------------------------------- underdog_boost


def test_underdog_boost_needs_the_gap(house: None) -> None:
    # "mine" lost both weeks, so it trails by two games.
    history = _history({"mine": 80.0, "theirs": 100.0}, {"mine": 70.0, "theirs": 90.0})
    enabled = {"underdog_boost": {"game_gap": 2, "pct": 0.10, "cap": 15}}

    context = _context(
        _line("RB"),
        base=20.0,
        team=_team("mine"),
        opponent=_team("theirs"),
        history=history,
    )
    assert evaluate(context, enabled) == {"underdog_boost": pytest.approx(2.0)}

    # The leader gets nothing.
    flipped = _context(
        _line("RB"),
        base=20.0,
        team=_team("theirs"),
        opponent=_team("mine"),
        history=history,
    )
    assert evaluate(flipped, enabled) == {}


def test_underdog_boost_is_capped(house: None) -> None:
    history = _history({"mine": 80.0, "theirs": 100.0}, {"mine": 70.0, "theirs": 90.0})
    context = _context(
        _line("QB"),
        base=400.0,
        team=_team("mine"),
        opponent=_team("theirs"),
        history=history,
    )
    enabled = {"underdog_boost": {"game_gap": 2, "pct": 0.10, "cap": 15}}
    assert evaluate(context, enabled) == {"underdog_boost": 15.0}


def test_underdog_boost_inert_in_week_one(house: None) -> None:
    """No completed weeks means no standings, so nobody is behind."""
    context = _context(_line("RB"), base=20.0, history=History())
    assert evaluate(context, {"underdog_boost": {"game_gap": 2}}) == {}


# --------------------------------------------------------------- hot_hand


def _streak(*bases: float) -> History:
    weeks = []
    for index, base in enumerate(bases, start=1):
        line = _line("RB", player_id="p1", week=index)
        team_weeks = {
            "mine": _team("mine", ScoredLine(line, base), week=index),
            "theirs": _team("theirs", week=index),
        }
        weeks.append(WeekResult(index, team_weeks, (Matchup(index, "mine", "theirs"),)))
    return History(tuple(weeks))


def test_hot_hand_needs_a_full_streak(house: None) -> None:
    enabled = {"hot_hand": {"weeks": 3, "threshold": 20, "bonus": 5}}
    hot = _context(_line("RB"), history=_streak(25.0, 22.0, 30.0))
    assert evaluate(hot, enabled) == {"hot_hand": 5.0}

    broken = _context(_line("RB"), history=_streak(25.0, 12.0, 30.0))
    assert evaluate(broken, enabled) == {}

    too_short = _context(_line("RB"), history=_streak(25.0, 30.0))
    assert evaluate(too_short, enabled) == {}


def test_hot_hand_uses_only_the_most_recent_weeks(house: None) -> None:
    """A cold patch early doesn't spoil a current streak."""
    enabled = {"hot_hand": {"weeks": 3, "threshold": 20, "bonus": 5}}
    context = _context(_line("RB"), history=_streak(1.0, 25.0, 22.0, 30.0))
    assert evaluate(context, enabled) == {"hot_hand": 5.0}


def test_hot_hand_ignores_rule_points(house: None) -> None:
    """Reading `.total` would let the bonus feed the streak that earns it."""
    weeks = []
    for index in range(1, 4):
        line = _line("RB", player_id="p1", week=index)
        scored = ScoredLine(line, base=18.0, rule_points={"hot_hand": 5.0})
        weeks.append(
            WeekResult(
                index,
                {"mine": _team("mine", scored, week=index), "theirs": _team("theirs", week=index)},
                (Matchup(index, "mine", "theirs"),),
            )
        )
    # Totals are 23 each, bases are 18. The streak must not count.
    context = _context(_line("RB"), history=History(tuple(weeks)))
    assert evaluate(context, {"hot_hand": {"weeks": 3, "threshold": 20}}) == {}


# --------------------------------------------------------------- YAML wiring


def test_candidate_ruleset_loads() -> None:
    ruleset = load_ruleset(RULES_DIR / "house_2026.yaml")
    assert ruleset.custom_rules.enabled["fg_long_bonus"]["bonus"] == 50
    assert ruleset.custom_rules.enabled["def_blowout_penalty"]["penalty"] == -40
    # Inherited from the base it extends.
    assert ruleset.scoring["receptions"] == 0.5
    assert set(ruleset.custom_rules.enabled) <= set(rules_mod.registered())


def test_enabling_an_unregistered_rule_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "candidate.yaml").write_text(
        "name: x\nlineup:\n  starters: [QB]\ncustom_rules:\n  enabled:\n    no_such_rule: {}\n"
    )
    with pytest.raises(ValueError, match="unregistered rule"):
        load_ruleset(tmp_path / "candidate.yaml")


def test_modules_resolve_relative_to_the_yaml(tmp_path: Path, monkeypatch) -> None:
    """A rule set must load the same way from any working directory."""
    monkeypatch.chdir(tmp_path)
    ruleset = load_ruleset(RULES_DIR / "house_2026.yaml")
    assert "fg_long_bonus" in ruleset.custom_rules.enabled
