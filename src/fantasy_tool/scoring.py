"""YAML rule sets and the base scorer.

A rule set is the YAML equivalent of Yahoo's Scoring Settings page: which categories
are enabled, what each is worth, and the league-wide fractional/negative switches.
Enabling a category means giving it a value; omitting it means the toggle is off.

Pydantic is here for the error messages, not the types. This file gets hand-edited
constantly while brainstorming rules, and "unknown stat 'recieving_yards' -- did you
mean 'receiving_yards'?" is worth the dependency. The validators are deliberately
strict: a typo that silently scored zero would corrupt a whole analysis while looking
entirely plausible.
"""

import math
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import rules
from .model import StatLine
from .stats import (
    BONUS_ELIGIBLE,
    MAX_BONUSES_PER_CATEGORY,
    MAX_OFFENSE_CATEGORIES,
    OFF,
    STAT_BY_KEY,
    YARDAGE_KEYS,
    suggest,
)

MERGED_SECTIONS = ("scoring", "yards_per_point", "bonuses")


def _check_scoring(value: dict[str, float]) -> dict[str, float]:
    for key in value:
        stat = STAT_BY_KEY.get(key)
        if stat is None:
            raise ValueError(f"unknown scoring category {key!r}.{suggest(key)}")
        if not stat.supported:
            raise ValueError(f"{key!r} ({stat.label}) is not scoreable from the store")
    return value


def _check_yards_per_point(value: dict[str, float]) -> dict[str, float]:
    for key, yards in value.items():
        stat = STAT_BY_KEY.get(key)
        if stat is None:
            raise ValueError(f"unknown scoring category {key!r}.{suggest(key)}")
        if key not in YARDAGE_KEYS:
            raise ValueError(
                f"{key!r} ({stat.label}) isn't measured in yards, so yards-per-point "
                f"makes no sense for it. Put it under `scoring`."
            )
        if yards <= 0:
            raise ValueError(f"{key!r}: yards per point must be positive, got {yards}")
    return value


def _as_points(scoring: dict[str, float], yards_per_point: dict[str, float]) -> dict[str, float]:
    """Both spellings, resolved to points per unit."""
    merged = dict(scoring)
    for key, yards in yards_per_point.items():
        merged[key] = 1.0 / yards
    return merged


class Bonus(BaseModel):
    """One threshold bonus, e.g. 100 rushing yards for +1. Cumulative with the rest."""

    model_config = ConfigDict(extra="forbid")
    target: float
    points: float


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fractional_points: bool = True
    negative_points: bool = True


class Lineup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starters: list[str]
    bench: int = 6


class PositionScoring(BaseModel):
    """What one position scores differently from everyone else.

    Yahoo added this for 2026: the offensive categories can each carry a different
    value per position. Before that, a category applied to every player alike, which
    is why a tight end could not be paid more per catch than a receiver without a
    custom rule and a weekly hand adjustment.

    Only the categories named here differ; everything else falls through to the
    league-wide value.
    """

    model_config = ConfigDict(extra="forbid")
    scoring: dict[str, float] = Field(default_factory=dict)
    yards_per_point: dict[str, float] = Field(default_factory=dict)

    @field_validator("scoring")
    @classmethod
    def _known_and_scoreable(cls, value: dict[str, float]) -> dict[str, float]:
        return _check_scoring(value)

    @field_validator("yards_per_point")
    @classmethod
    def _yardage_categories_only(cls, value: dict[str, float]) -> dict[str, float]:
        return _check_yards_per_point(value)

    @model_validator(mode="after")
    def _offense_only_and_unambiguous(self) -> "PositionScoring":
        clash = set(self.scoring) & set(self.yards_per_point)
        if clash:
            raise ValueError(
                f"{', '.join(sorted(clash))} given both as points-per-yard and "
                f"yards-per-point; pick one"
            )
        for key in self.points:
            if STAT_BY_KEY[key].section != "Offense":
                raise ValueError(
                    f"{key!r} ({STAT_BY_KEY[key].label}) is not an offensive category. "
                    f"Yahoo varies only offensive categories by position."
                )
        return self

    @property
    def points(self) -> dict[str, float]:
        return _as_points(self.scoring, self.yards_per_point)


class CustomRules(BaseModel):
    """Python rules for what the settings page can't express. See rules.py."""

    model_config = ConfigDict(extra="forbid")
    modules: list[Path] = Field(default_factory=list)
    enabled: dict[str, dict[str, float | int | str | bool]] = Field(default_factory=dict)


class RuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    lineup: Lineup
    options: Options = Field(default_factory=Options)
    scoring: dict[str, float] = Field(default_factory=dict)
    # Yardage categories, written the way Yahoo asks for them: how many yards make a
    # point. Yahoo's settings page has no box for 0.1325 a yard -- it asks for 7.5
    # yards -- so a config that mirrors the interface can be transcribed either way
    # without anyone doing arithmetic and getting it wrong.
    yards_per_point: dict[str, float] = Field(default_factory=dict)
    bonuses: dict[str, list[Bonus]] = Field(default_factory=dict)
    # Offensive categories that pay one position differently from the rest. New for
    # 2026; before that every category applied to every player alike.
    positions: dict[str, PositionScoring] = Field(default_factory=dict)
    custom_rules: CustomRules = Field(default_factory=CustomRules)

    @field_validator("scoring")
    @classmethod
    def _known_and_scoreable(cls, value: dict[str, float]) -> dict[str, float]:
        return _check_scoring(value)

    @field_validator("yards_per_point")
    @classmethod
    def _yardage_categories_only(cls, value: dict[str, float]) -> dict[str, float]:
        return _check_yards_per_point(value)

    @field_validator("positions")
    @classmethod
    def _real_positions(cls, value: dict[str, PositionScoring]) -> dict[str, PositionScoring]:
        for position in value:
            if position not in OFF:
                raise ValueError(
                    f"{position!r} can't have its own scoring. Yahoo varies categories by "
                    f"position for {', '.join(sorted(OFF))} only."
                )
        return value

    @field_validator("bonuses")
    @classmethod
    def _bonuses_are_legal(cls, value: dict[str, list[Bonus]]) -> dict[str, list[Bonus]]:
        for key, bonuses in value.items():
            if key not in BONUS_ELIGIBLE:
                raise ValueError(
                    f"Yahoo allows bonuses only on {', '.join(BONUS_ELIGIBLE)}; got {key!r}"
                )
            if len(bonuses) > MAX_BONUSES_PER_CATEGORY:
                raise ValueError(
                    f"{key!r} has {len(bonuses)} bonuses; Yahoo allows {MAX_BONUSES_PER_CATEGORY}"
                )
            targets = [b.target for b in bonuses]
            if targets != sorted(targets) or len(set(targets)) != len(targets):
                raise ValueError(f"{key!r} bonus targets must be ascending and distinct")
        return value

    @model_validator(mode="after")
    def _within_yahoo_limits(self) -> "RuleSet":
        clash = set(self.scoring) & set(self.yards_per_point)
        if clash:
            raise ValueError(
                f"{', '.join(sorted(clash))} given both as points-per-yard and "
                f"yards-per-point; pick one"
            )
        for position in (None, *sorted(self.positions)):
            enabled = self.points_for(position)
            offense = [k for k in enabled if STAT_BY_KEY[k].section == "Offense"]
            if len(offense) > MAX_OFFENSE_CATEGORIES:
                whose = f"for {position}" if position else "league-wide"
                raise ValueError(
                    f"{len(offense)} offensive categories enabled {whose}; Yahoo caps "
                    f"this at {MAX_OFFENSE_CATEGORIES}"
                )
        for key in self.bonuses:
            if key not in self.points:
                raise ValueError(f"bonus on {key!r} but that category isn't enabled")
        return self

    @property
    def points(self) -> dict[str, float]:
        """Every league-wide category as points per unit, whichever way it was written."""
        return _as_points(self.scoring, self.yards_per_point)

    def points_for(self, position: str | None) -> dict[str, float]:
        """The same, as this position is actually paid.

        Anything the position doesn't override falls through to the league-wide value,
        so a config lists only the differences -- which is also how Yahoo's page reads.
        """
        override = self.positions.get(position or "")
        if override is None:
            return self.points
        return {**self.points, **override.points}

    @property
    def starter_count(self) -> int:
        return len(self.lineup.starters)


def _merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge, one level deep. `scoring` and `bonuses` merge key by key.

    Deliberately not recursive *within* a section: a rule set should read as "the one
    it extends, plus these specific changes", and deep-merge semantics make that
    harder to reason about than they're worth. To turn a category off, set it to null.
    Sections other than those two are replaced wholesale -- `positions` included, so a
    file that varies scoring by position restates the whole block rather than editing
    one line of a parent's.
    """
    merged = dict(parent)
    for section, value in child.items():
        if section in MERGED_SECTIONS and isinstance(value, dict):
            combined = dict(parent.get(section) or {})
            combined.update(value)
            merged[section] = {k: v for k, v in combined.items() if v is not None}
        else:
            merged[section] = value
    return merged


MAX_EXTENDS_DEPTH = 10


def load_ruleset(path: str | Path) -> RuleSet:
    """Read a rule set, resolving `extends:` and importing any custom rule modules.

    Chains are allowed -- base to superflex to balanced is a natural way to build up
    variants -- and are applied root first so the most derived file wins.

    Module paths are relative to the file that declared them, not the working
    directory, so a rule set stays valid however it's invoked.
    """
    path = Path(path).resolve()

    chain: list[tuple[Path, dict[str, Any]]] = []
    seen: set[Path] = set()
    current: Path | None = path
    while current is not None:
        if current in seen:
            raise ValueError(f"{current.name} extends itself, directly or in a cycle")
        if len(chain) >= MAX_EXTENDS_DEPTH:
            raise ValueError(f"{path.name}: extends chain deeper than {MAX_EXTENDS_DEPTH}")
        seen.add(current)
        data = yaml.safe_load(current.read_text()) or {}
        parent_ref = data.pop("extends", None)
        chain.append((current, data))
        current = (current.parent / parent_ref).resolve() if parent_ref else None

    merged: dict[str, Any] = {}
    modules_from = path
    for source, data in reversed(chain):
        if "custom_rules" in data:
            # Replaced wholesale, so the last file to declare it owns the paths.
            modules_from = source
        merged = _merge(merged, data)

    ruleset = RuleSet(**merged)

    if ruleset.custom_rules.modules:
        rules.load_modules(modules_from.parent / module for module in ruleset.custom_rules.modules)

    known = rules.registered()
    unknown = [name for name in ruleset.custom_rules.enabled if name not in known]
    if unknown:
        available = ", ".join(sorted(known)) or "none"
        raise ValueError(
            f"{path.name} enables unregistered rule(s) {', '.join(unknown)}. "
            f"Registered: {available}"
        )

    return ruleset


def _yardage_points(raw: float, options: Options) -> float:
    """Apply the league-wide fractional and negative switches to a yardage total."""
    if not options.negative_points and raw < 0:
        return 0.0
    if not options.fractional_points:
        # Whole points only. Truncates toward zero, so -1.8 becomes -1.
        return float(math.trunc(raw))
    return raw


def score_standalone(line: StatLine, rules: RuleSet) -> float:
    """What a player is worth judged on his own, custom rules included.

    Used for draft valuation and weekly projections, where the question is "how good
    is this player" rather than "what happened in this matchup". Rules that depend on
    the rest of the league -- standings, opponents, teammates -- see an empty context
    and contribute nothing, which is the right approximation: they describe situations,
    not players.

    Scoring valuation with `score_base` instead would leave managers ignorant of any
    custom rule. A tight end premium would then go unnoticed by every manager in the
    league, and the position it was written to promote would stay on the bench.
    """
    base = score_base(line, rules)
    enabled = rules.custom_rules.enabled
    if not enabled:
        return base

    from .model import History, TeamWeek
    from .rules import RuleContext, evaluate

    empty = TeamWeek(team="", week=line.week, scored=())
    context = RuleContext(
        line=line, base=base, params={}, team=empty, opponent=empty, history=History()
    )
    return base + sum(evaluate(context, enabled).values())


def score_base(line: StatLine, rules: RuleSet) -> float:
    """Points this player-week earns under the YAML rules alone.

    Every enabled category contributes `multiplier * stat`, plus any threshold
    bonuses. Multipliers come from `points_for`, so an offensive category that pays
    this position differently is picked up here. The only category a line is excluded
    from is one belonging to the other kind of entity: team-defense categories never
    apply to a player, and player categories never apply to a team unit. Notably a
    kicker is *not* restricted to kicking categories -- fake field goals happen, and
    Yahoo pays out for them.
    """
    total = 0.0
    is_team_unit = line.position == "DEF"

    for key, multiplier in rules.points_for(line.position).items():
        stat = STAT_BY_KEY[key]
        if stat.is_team_defense != is_team_unit:
            continue
        points = multiplier * line.s(stat.column)
        total += _yardage_points(points, rules.options) if key in YARDAGE_KEYS else points

    for key, bonuses in rules.bonuses.items():
        stat = STAT_BY_KEY[key]
        if stat.is_team_defense != is_team_unit:
            continue
        value = line.s(stat.column)
        total += sum(bonus.points for bonus in bonuses if value >= bonus.target)

    return total
