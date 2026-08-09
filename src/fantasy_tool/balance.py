"""Making positions worth the same, using only increases.

In a flex-heavy lineup a manager choosing between a quarterback and a tight end for
the same slot is comparing raw points. So "equal value" here means the average
startable player at each position scores about the same per game, which is what makes
a flex slot position-neutral.

Two facts from the data shape everything:

Quarterbacks score most and nothing raises the others past them cheaply, so with
increases only, the quarterback average is the target the rest climb to.

Tight ends are out-produced by receivers on *every* per-game receiving stat -- yards,
receptions, touchdowns, even first downs per catch. Since Yahoo's categories apply to
all players alike, no Yahoo-native change can raise tight ends relative to receivers.
That gap needs a per-position rule, and no amount of tuning the shared categories
substitutes for one.
"""

from dataclasses import dataclass, field
from statistics import mean

from .model import StatLine
from .scoring import RuleSet, score_base
from .store import DEFAULT_ROOT, load_statlines

FLEX_POSITIONS = ("QB", "RB", "WR", "TE")
MIN_GAMES = 8  # enough to be a real starter rather than a hot week


@dataclass(frozen=True, slots=True)
class PositionProfile:
    """What the startable players at one position do in an average game."""

    position: str
    mean_points: float
    mean_stats: dict[str, float]
    pool_size: int


@dataclass(frozen=True, slots=True)
class Solution:
    target: float
    increments: dict[str, float] = field(default_factory=dict)  # Yahoo category -> increase
    premiums: dict[str, tuple[str, float]] = field(default_factory=dict)  # position -> (stat, rate)
    achieved: dict[str, float] = field(default_factory=dict)

    @property
    def feasible(self) -> bool:
        """Every increment must be non-negative, since nothing may be reduced."""
        return all(v >= -1e-9 for v in self.increments.values()) and all(
            rate >= -1e-9 for _, rate in self.premiums.values()
        )

    @property
    def spread(self) -> float:
        return max(self.achieved.values()) - min(self.achieved.values()) if self.achieved else 0.0


def startable_pool(teams: int, flex_share: float = 1.0) -> int:
    """How many players per position the league would start if the four were equal.

    One dedicated slot plus an equal share of the flex slots. That is the balanced
    state being aimed at, so it is the right pool to measure against -- measuring the
    pool the *current* rules produce would bake today's imbalance into the target.
    """
    return round(teams * (1 + flex_share))


def profile(
    seasons,
    rules: RuleSet,
    stats: list[str],
    *,
    top_n: int,
    root=DEFAULT_ROOT,
) -> dict[str, PositionProfile]:
    """Per-game averages for the top `top_n` players at each flex position."""
    per_player: dict[tuple[str, str], list[StatLine]] = {}
    for season in seasons:
        for (player_id, _), line in load_statlines(season, root=root).items():
            if line.position in FLEX_POSITIONS:
                per_player.setdefault((f"{season}:{player_id}", line.position), []).append(line)

    profiles: dict[str, PositionProfile] = {}
    for position in FLEX_POSITIONS:
        rated = [
            (mean(score_base(line, rules) for line in lines), lines)
            for (_, pos), lines in per_player.items()
            if pos == position and len(lines) >= MIN_GAMES
        ]
        top = sorted(rated, key=lambda pair: pair[0], reverse=True)[:top_n]
        if not top:
            continue
        profiles[position] = PositionProfile(
            position=position,
            mean_points=mean(points for points, _ in top),
            mean_stats={
                key: mean(mean(line.s(key) for line in lines) for _, lines in top) for key in stats
            },
            pool_size=len(top),
        )
    return profiles


def _solve_linear(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting. Small dense systems only."""
    size = len(vector)
    rows = [row[:] + [vector[i]] for i, row in enumerate(matrix)]

    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        if abs(rows[pivot][column]) < 1e-12:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for row in range(size):
            if row == column:
                continue
            factor = rows[row][column] / rows[column][column]
            for col in range(column, size + 1):
                rows[row][col] -= factor * rows[column][col]

    return [rows[i][size] / rows[i][i] for i in range(size)]


def solve(
    profiles: dict[str, PositionProfile],
    levers: list[str],
    premium: tuple[str, str] | None = None,
    positions: tuple[str, ...] = FLEX_POSITIONS,
) -> Solution | None:
    """Find increases that bring the given positions to the same average.

    `levers` are Yahoo categories raised for everyone. `premium` is an optional
    (position, stat) pair scored only for that position, which a custom rule provides
    because Yahoo cannot. There must be exactly one unknown per position being lifted.

    `positions` should be the ones that actually compete for a slot. A position with
    only a dedicated slot doesn't need balancing at all: every team starts exactly one,
    so scoring less is symmetric and costs nobody anything. It only matters when a
    position has to win a flex slot against the others.
    """
    ranked = {p: profiles[p] for p in positions if p in profiles}
    anchor = max(ranked.values(), key=lambda p: p.mean_points).position
    others = [p for p in positions if p != anchor and p in profiles]
    unknowns = [*levers] + ([premium] if premium else [])
    if len(unknowns) != len(others):
        return None

    def coefficient(position: str, unknown) -> float:
        if isinstance(unknown, tuple):
            premium_position, stat = unknown
            return (
                profiles[position].mean_stats.get(stat, 0.0)
                if position == premium_position
                else 0.0
            )
        return profiles[position].mean_stats.get(unknown, 0.0)

    # For each lifted position: value(p) + sum(delta * stat) == value(anchor) + same.
    matrix, vector = [], []
    for position in others:
        matrix.append([coefficient(position, u) - coefficient(anchor, u) for u in unknowns])
        vector.append(profiles[anchor].mean_points - profiles[position].mean_points)

    solved = _solve_linear(matrix, vector)
    if solved is None:
        return None

    increments = {lever: solved[index] for index, lever in enumerate(levers)}
    premiums = {}
    if premium:
        premium_position, stat = premium
        premiums[premium_position] = (stat, solved[-1])

    achieved = {}
    for position, position_profile in profiles.items():
        total = position_profile.mean_points
        for lever, delta in increments.items():
            total += delta * position_profile.mean_stats.get(lever, 0.0)
        if position in premiums:
            stat, rate = premiums[position]
            total += rate * position_profile.mean_stats.get(stat, 0.0)
        achieved[position] = total

    return Solution(
        target=achieved[anchor],
        increments=increments,
        premiums=premiums,
        achieved={p: v for p, v in achieved.items() if p in positions},
    )
