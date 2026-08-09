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

from .model import StatLine
from .stats import (
    BONUS_ELIGIBLE,
    MAX_BONUSES_PER_CATEGORY,
    MAX_OFFENSE_CATEGORIES,
    STAT_BY_KEY,
    YARDAGE_KEYS,
    suggest,
)

MERGED_SECTIONS = ("scoring", "bonuses")


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


class RuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    lineup: Lineup
    options: Options = Field(default_factory=Options)
    scoring: dict[str, float] = Field(default_factory=dict)
    bonuses: dict[str, list[Bonus]] = Field(default_factory=dict)

    @field_validator("scoring")
    @classmethod
    def _known_and_scoreable(cls, value: dict[str, float]) -> dict[str, float]:
        for key in value:
            stat = STAT_BY_KEY.get(key)
            if stat is None:
                raise ValueError(f"unknown scoring category {key!r}.{suggest(key)}")
            if not stat.supported:
                raise ValueError(f"{key!r} ({stat.label}) is not scoreable from the store")
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
        offense = [k for k in self.scoring if STAT_BY_KEY[k].section == "Offense"]
        if len(offense) > MAX_OFFENSE_CATEGORIES:
            raise ValueError(
                f"{len(offense)} offensive categories enabled; Yahoo caps this at "
                f"{MAX_OFFENSE_CATEGORIES}"
            )
        for key in self.bonuses:
            if key not in self.scoring:
                raise ValueError(f"bonus on {key!r} but that category isn't enabled")
        return self

    @property
    def starter_count(self) -> int:
        return len(self.lineup.starters)


def _merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge, one level deep. `scoring` and `bonuses` merge key by key.

    Deliberately not recursive: a candidate rule set should be readable as "the base,
    plus these specific changes", and deep-merge semantics make that harder to reason
    about than they're worth. To turn a category off, set it to null.
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


def load_ruleset(path: str | Path) -> RuleSet:
    """Read a rule set, resolving a single level of `extends:`."""
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}

    parent_ref = data.pop("extends", None)
    if parent_ref:
        parent_path = (path.parent / parent_ref).resolve()
        parent = yaml.safe_load(parent_path.read_text()) or {}
        if "extends" in parent:
            raise ValueError(f"{parent_path} itself extends another file; only one level")
        data = _merge(parent, data)

    return RuleSet(**data)


def _yardage_points(raw: float, options: Options) -> float:
    """Apply the league-wide fractional and negative switches to a yardage total."""
    if not options.negative_points and raw < 0:
        return 0.0
    if not options.fractional_points:
        # Whole points only. Truncates toward zero, so -1.8 becomes -1.
        return float(math.trunc(raw))
    return raw


def score_base(line: StatLine, rules: RuleSet) -> float:
    """Points this player-week earns under the YAML rules alone.

    Every enabled category contributes `multiplier * stat`, plus any threshold
    bonuses. The only category a line is excluded from is one belonging to the other
    kind of entity: team-defense categories never apply to a player, and player
    categories never apply to a team unit. Notably a kicker is *not* restricted to
    kicking categories -- fake field goals happen, and Yahoo pays out for them.
    """
    total = 0.0
    is_team_unit = line.position == "DEF"

    for key, multiplier in rules.scoring.items():
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
