"""Custom Python rules, for what Yahoo's settings page can't express.

Yahoo can only multiply a stat by a number and add three yardage bonuses. Anything
conditional -- a penalty that fires past a threshold, a boost that depends on the
standings, a streak -- has to be code. A rule is a function that receives one
player-week in context and returns a point delta.

Two constraints are deliberate:

Rules see their teammates and opponents *base-scored only*, never other rules'
output. That makes rules order-independent, which removes a whole class of
"which one ran first" bugs. The cost is that a rule can't react to another rule,
which nobody has wanted.

Rules receive only completed weeks in `history`, so a rule cannot see the future.
That is enforced by what's in the object rather than by convention.
"""

import importlib.util
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from .model import POSITIONS, History, StatLine, TeamWeek

ParamValue = float | int | str | bool
Params = dict[str, ParamValue]


@dataclass(frozen=True, slots=True)
class RuleContext:
    """Everything a rule is allowed to know."""

    line: StatLine  # the player-week being scored
    base: float  # its points under the YAML rules alone
    params: Params  # from custom_rules.enabled[<name>] in the YAML
    team: TeamWeek  # the manager's own starters, base-scored
    opponent: TeamWeek  # this week's opposing starters, base-scored
    history: History  # completed weeks only

    def param(self, key: str, default: ParamValue) -> ParamValue:
        return self.params.get(key, default)


RuleFn = Callable[[RuleContext], float]


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    positions: frozenset[str]
    fn: RuleFn

    @property
    def doc(self) -> str:
        return (self.fn.__doc__ or "").strip().split("\n")[0]


_REGISTRY: dict[str, Rule] = {}
_LOADED: set[Path] = set()


def rule(name: str, *, positions: Iterable[str] | None = None) -> Callable[[RuleFn], RuleFn]:
    """Register a scoring rule, called once per rostered starter per week.

    `positions` narrows which lines it runs against; omit it to run for everyone.
    """

    def decorate(fn: RuleFn) -> RuleFn:
        existing = _REGISTRY.get(name)
        if existing is not None and existing.fn is not fn:
            raise ValueError(f"two different rules are both named {name!r}")
        for position in positions or ():
            if position not in POSITIONS:
                raise ValueError(f"rule {name!r}: unknown position {position!r}")
        _REGISTRY[name] = Rule(name, frozenset(positions or POSITIONS), fn)
        return fn

    return decorate


def registered() -> dict[str, Rule]:
    return dict(_REGISTRY)


def clear_registry() -> None:
    """Drop every registered rule. For tests; production loads modules once."""
    _REGISTRY.clear()
    _LOADED.clear()


def load_modules(paths: Iterable[Path]) -> None:
    """Import rule modules by file path, at most once each.

    Plain importlib rather than entry points or package discovery: a rule file lives
    next to the YAML that enables it and is edited in the same breath.

    Loading is idempotent because a single run routinely loads the same file more than
    once -- a baseline and a candidate that share a module, or a sweep over a range of
    parameters. Re-executing would build fresh function objects and trip the
    duplicate-name guard, which is meant for two genuinely different rules claiming one
    name, not for a file being read twice.
    """
    for path in paths:
        resolved = Path(path).resolve()
        if resolved in _LOADED:
            continue
        if not resolved.exists():
            raise FileNotFoundError(f"rule module {resolved} not found")
        spec = importlib.util.spec_from_file_location(resolved.stem, resolved)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot import {resolved}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[resolved.stem] = module
        spec.loader.exec_module(module)
        _LOADED.add(resolved)


def evaluate(context: RuleContext, enabled: dict[str, Params]) -> dict[str, float]:
    """Run the enabled rules against one line; returns name -> point delta.

    Rules contributing nothing are left out, so `rule_points` stays a record of what
    actually fired rather than a row of zeros.
    """
    deltas: dict[str, float] = {}
    for name, params in enabled.items():
        current = _REGISTRY.get(name)
        if current is None:
            raise KeyError(f"rule {name!r} is enabled but not registered")
        if context.line.position not in current.positions:
            continue
        delta = current.fn(replace(context, params=params))
        if delta:
            deltas[name] = float(delta)
    return deltas
