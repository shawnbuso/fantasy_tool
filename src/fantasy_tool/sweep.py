"""Varying a rule's parameters to find a magnitude that isn't absurd.

Knowing a rule is too swingy is only half an answer; the useful half is what number to
use instead. A sweep re-runs the same leagues across a range of values and reports how
each one lands, so the choice is made from evidence rather than argued about.
"""

from dataclasses import dataclass
from itertools import pairwise, product

from .analysis import Analysis, compare
from .harness import run_pairs
from .model import League
from .scoring import RuleSet

# A parameter setting under test, e.g. ("def_blowout_penalty", "penalty", -12.0).
Setting = tuple[str, str, float | int | str | bool]


@dataclass(frozen=True, slots=True)
class SweepPoint:
    settings: tuple[Setting, ...]
    analysis: Analysis

    @property
    def label(self) -> str:
        return ", ".join(
            f"{key.rsplit('.', 1)[-1]}={_show(value)}" for _, key, value in self.settings
        )

    @property
    def swept(self) -> set[str]:
        return {rule_name for rule_name, _, _ in self.settings}

    @property
    def impact(self):
        """The rule being tuned, not the whole bundle.

        A candidate rule set usually has several rules enabled, and the others don't
        change as this one is swept. Judging by the combined effect would let a
        different rule's verdict drown out the one under test -- every setting would
        read AUTO-DECIDE because something else in the file already does.
        """
        names = self.swept
        if len(names) == 1:
            name = next(iter(names))
            for impact in self.analysis.per_rule:
                if impact.name == name:
                    return impact
        return self.analysis.overall


def _show(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


NATIVE = ("scoring", "yards_per_point", "bonus")


def parse_spec(spec: str) -> tuple[str, str, list]:
    """Parse a sweep target and its values.

    Four shapes, because a league's knobs live in four places:

        fg_long_bonus.bonus=5,10,20        a custom rule's parameter
        scoring.ya_0_99=10,20,30           a Yahoo category
        yards_per_point.rushing_yards=7,8  a Yahoo yardage rate
        bonus.passing_yards.500=10,20,30   one tier of a Yahoo yardage bonus
    """
    target, _, values = spec.partition("=")
    if not values:
        raise ValueError(f"expected target=value1,value2 but got {spec!r}")
    namespace, _, rest = target.partition(".")
    if not rest:
        raise ValueError(f"expected <rule or section>.<name>=... but got {target!r}")
    namespace = namespace.strip()
    if namespace == "bonus" and "." not in rest:
        raise ValueError("bonus sweeps need a tier target, e.g. bonus.passing_yards.500=...")
    return namespace, rest.strip(), [_coerce(v.strip()) for v in values.split(",")]


def _coerce(text: str):
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def apply_settings(ruleset: RuleSet, settings: tuple[Setting, ...]) -> RuleSet:
    """Return a copy of the rule set with each swept value applied."""
    enabled = {name: dict(params) for name, params in ruleset.custom_rules.enabled.items()}
    scoring = dict(ruleset.scoring)
    yards = dict(ruleset.yards_per_point)
    bonuses = {k: [b.model_copy() for b in v] for k, v in ruleset.bonuses.items()}

    for namespace, path, value in settings:
        if namespace == "scoring":
            if path not in scoring:
                raise ValueError(f"{path!r} is not an enabled scoring category")
            scoring[path] = float(value)
        elif namespace == "yards_per_point":
            if path not in yards:
                raise ValueError(f"{path!r} is not scored by yards per point here")
            yards[path] = float(value)
        elif namespace == "bonus":
            category, _, target = path.partition(".")
            tiers = bonuses.get(category)
            if not tiers:
                raise ValueError(f"no bonuses on {category!r}")
            match = [b for b in tiers if b.target == float(target)]
            if not match:
                available = ", ".join(f"{b.target:g}" for b in tiers)
                raise ValueError(f"{category!r} has no {target} tier. Targets: {available}")
            match[0].points = float(value)
        else:
            if namespace not in enabled:
                available = ", ".join(sorted(enabled)) or "none"
                raise ValueError(
                    f"{namespace!r} is not an enabled rule or known section. "
                    f"Enabled rules: {available}"
                )
            enabled[namespace][path] = value

    return ruleset.model_copy(
        update={
            "scoring": scoring,
            "yards_per_point": yards,
            "bonuses": bonuses,
            "custom_rules": ruleset.custom_rules.model_copy(update={"enabled": enabled}),
        }
    )


def run(
    leagues: list[League],
    baseline: RuleSet,
    candidate: RuleSet,
    specs: list[str],
    *,
    skills: list[dict[str, float]] | None = None,
    progress=None,
) -> list[SweepPoint]:
    """Evaluate every combination of the given parameter values.

    The baseline is simulated once and shared, since it doesn't depend on the
    parameters being varied.
    """
    parsed = [parse_spec(spec) for spec in specs]
    combinations = list(product(*[[(r, p, v) for v in values] for r, p, values in parsed]))

    from .sim import simulate

    baseline_results = [simulate(league, baseline) for league in leagues]

    points = []
    for index, settings in enumerate(combinations):
        if progress:
            label = ", ".join(f"{k.rsplit('.', 1)[-1]}={_show(v)}" for _, k, v in settings)
            progress(f"Testing {label} ({index + 1} of {len(combinations)})...")
        variant = apply_settings(candidate, settings)
        pairs = run_pairs(leagues, baseline, variant, baseline_results=baseline_results)
        points.append(SweepPoint(settings, compare(pairs, baseline, variant, skills)))
    return points


def recommend(points: list[SweepPoint]) -> SweepPoint | None:
    """The strongest setting that still isn't dominating the league.

    "Strongest" is the largest average effect when the rule fires, among settings whose
    verdict is not AUTO-DECIDE or LOTTERY TICKET. If every setting is too swingy this
    returns nothing, which is itself the answer: the rule needs rethinking, not tuning.
    """
    acceptable = [
        point for point in points if point.impact.verdict not in ("AUTO-DECIDE", "LOTTERY TICKET")
    ]
    if not acceptable:
        return None
    return max(acceptable, key=lambda p: p.impact.mean_when_fired)


def is_monotonic(points: list[SweepPoint]) -> bool:
    """Whether the flip rate rises with the setting, as it should for a single sweep.

    A jagged curve means noise is swamping the signal, which means too few leagues --
    worth saying out loud rather than letting someone read a ranking off it.

    Only meaningful along one axis. A grid over several parameters is a product, so a
    flattened ordering is non-monotonic by construction and warning about it would be
    a false alarm.
    """
    if len(points) < 3:
        return True
    if len({(rule, key) for p in points for rule, key, _ in p.settings}) > 1:
        return True
    rates = [p.impact.flips.rate for p in points]
    rising = all(b >= a - 1e-9 for a, b in pairwise(rates))
    falling = all(b <= a + 1e-9 for a, b in pairwise(rates))
    return rising or falling
