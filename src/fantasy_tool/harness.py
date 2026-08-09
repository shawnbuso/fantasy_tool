"""Building and running the leagues a comparison needs.

Shared by `evaluate` and `sweep`. The important property is that leagues are built
once from the baseline and reused: every candidate under test is simulated against
exactly the same drafts, schedules, and lineups, so differences between candidates are
differences between rules and nothing else. It is also what makes a sweep affordable,
since generating a league costs far more than replaying one.
"""

from collections.abc import Iterable
from pathlib import Path

from .model import League, SeasonResult
from .scoring import RuleSet
from .sim import simulate
from .sources.synthetic import DEFAULT_TEAMS, build_pool, generate
from .store import DEFAULT_ROOT


def build_leagues(
    seasons: Iterable[int],
    baseline: RuleSet,
    *,
    leagues: int,
    seed: int = 7,
    teams: int = DEFAULT_TEAMS,
    root: Path = DEFAULT_ROOT,
    progress=None,
) -> list[League]:
    """Generate `leagues` synthetic leagues for each season, valued off the baseline."""
    built: list[League] = []
    for season in seasons:
        if progress:
            progress(f"Season {season}: building player pool...")
        pool = build_pool(season, baseline, root=root, teams=teams)
        for index in range(leagues):
            if progress:
                progress(f"Season {season}: league {index + 1} of {leagues}...")
            built.append(generate(pool, seed + index, baseline, n_teams=teams))
    return built


def run_pairs(
    leagues: list[League],
    baseline: RuleSet,
    candidate: RuleSet,
    *,
    baseline_results: list[SeasonResult] | None = None,
) -> list[tuple[SeasonResult, SeasonResult]]:
    """Simulate each league under both rule sets.

    `baseline_results` lets a sweep replay the baseline once and reuse it across every
    candidate, which is most of the saving.
    """
    base = baseline_results or [simulate(league, baseline) for league in leagues]
    return [(b, simulate(league, candidate)) for league, b in zip(leagues, base, strict=True)]


def skills(leagues: list[League]) -> list[dict[str, float]]:
    return [league.meta["skill"] for league in leagues]
