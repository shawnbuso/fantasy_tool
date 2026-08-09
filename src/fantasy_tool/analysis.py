"""Comparing two rule sets over the same leagues.

Everything here rests on one idea: run the identical league twice, changing only the
rules, and attribute the difference to the rule. That only works if the two runs share
a draft, a schedule, and lineups, which is why the league is generated once from the
baseline and simulated twice.

The headline number is `decisive_rate`: given that a rule fired, how often was its
swing bigger than the margin of the game it fired in. That is the direct formalisation
of "this rule decides matches". It is more stable than a flip rate, which additionally
requires the rule to land on the losing side, and it stays comparable across rules
that fire at very different frequencies.
"""

import math
import statistics as st
from dataclasses import dataclass, field

from .model import SeasonResult
from .scoring import RuleSet

Z_95 = 1.959963985

# Below this, a swing is nothing. Rules that pay both sides equally cancel to a
# floating-point residue rather than a clean zero, and without a tolerance every such
# matchup would be counted as triggered -- inflating the denominator of the headline
# rate with games where the rule provably changed nothing.
NEGLIGIBLE = 1e-9


def _sign(value: float) -> int:
    """-1, 0 or 1, treating anything below the tolerance as a tie."""
    if abs(value) <= NEGLIGIBLE:
        return 0
    return 1 if value > 0 else -1


# --------------------------------------------------------------------- stats


@dataclass(frozen=True, slots=True)
class Rate:
    """A proportion with a confidence interval, because sample size matters here.

    A rule firing 6% of the time in one season gives four events; quoting a bare rate
    off that would be worse than not measuring it.
    """

    successes: int
    total: int

    @property
    def rate(self) -> float:
        return self.successes / self.total if self.total else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        """Wilson score interval, which behaves sensibly near 0 and 1."""
        if not self.total:
            return (0.0, 0.0)
        n, p = self.total, self.rate
        denominator = 1 + Z_95**2 / n
        centre = (p + Z_95**2 / (2 * n)) / denominator
        spread = Z_95 * math.sqrt(p * (1 - p) / n + Z_95**2 / (4 * n**2)) / denominator
        return (max(0.0, centre - spread), min(1.0, centre + spread))


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        average = (index + end) / 2 + 1
        for position in range(index, end + 1):
            ranks[order[position]] = average
        index = end + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    spread_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    spread_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    return covariance / (spread_x * spread_y) if spread_x and spread_y else 0.0


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation. Used throughout because the relationships aren't linear."""
    return _pearson(_ranks(xs), _ranks(ys)) if len(xs) >= 2 else 0.0


# --------------------------------------------------------------------- diffs


@dataclass(frozen=True, slots=True)
class MatchupDiff:
    league_key: str
    week: int
    home: str
    away: str
    home_base: float
    away_base: float
    home_candidate: float
    away_candidate: float
    # Net effect of each rule on this matchup, from the home team's perspective.
    rule_swing: dict[str, float] = field(default_factory=dict)

    @property
    def margin_base(self) -> float:
        return self.home_base - self.away_base

    @property
    def margin_candidate(self) -> float:
        return self.home_candidate - self.away_candidate

    @property
    def swing(self) -> float:
        return self.margin_candidate - self.margin_base

    @property
    def triggered(self) -> bool:
        return any(abs(v) > NEGLIGIBLE for v in self.rule_swing.values())

    @property
    def flipped(self) -> bool:
        """Did the winner change? Ties are a third state, never silently a win.

        The comparison is tolerant, because nobody wins a fantasy matchup by 1e-14.
        Summing a lineup in floating point leaves dust, and a strict sign test reads a
        genuine tie broken by that dust as a flipped result -- inflating the flip rate
        on precisely the closest games, which are the ones a rule gets blamed for.
        """
        return _sign(self.margin_base) != _sign(self.margin_candidate)

    @property
    def decisive(self) -> bool:
        """Was the rule's swing bigger than the game it landed in?"""
        return abs(self.swing) > abs(self.margin_base)


def _rule_swings(
    base: SeasonResult, candidate: SeasonResult, week: int, home: str, away: str
) -> dict[str, float]:
    del base
    swings: dict[str, float] = {}
    for team, direction in ((home, 1.0), (away, -1.0)):
        for line in candidate.team_week(team, week).scored:
            for name, delta in line.rule_points.items():
                swings[name] = swings.get(name, 0.0) + direction * delta
    return swings


def diff_season(base: SeasonResult, candidate: SeasonResult) -> list[MatchupDiff]:
    if base.key != candidate.key:
        raise ValueError(f"comparing different leagues: {base.key} vs {candidate.key}")

    diffs = []
    for base_week, candidate_week in zip(base.weeks, candidate.weeks, strict=True):
        for matchup in base_week.matchups:
            home, away = matchup.home, matchup.away
            diffs.append(
                MatchupDiff(
                    league_key=base.key,
                    week=matchup.week,
                    home=home,
                    away=away,
                    home_base=base_week.team_weeks[home].points,
                    away_base=base_week.team_weeks[away].points,
                    home_candidate=candidate_week.team_weeks[home].points,
                    away_candidate=candidate_week.team_weeks[away].points,
                    rule_swing=_rule_swings(base, candidate, matchup.week, home, away),
                )
            )
    return diffs


# ------------------------------------------------------------------- metrics


@dataclass(frozen=True, slots=True)
class RuleImpact:
    name: str
    fired: Rate  # over team-weeks
    total_points: float
    mean_when_fired: float
    decisive: Rate  # over matchups it triggered
    flips: Rate  # over all matchups
    median_swing: float

    @property
    def verdict(self) -> str:
        rate = self.decisive.rate
        if self.fired.rate < 0.02 and rate > 0.5:
            return "LOTTERY TICKET"
        if rate >= 0.50:
            return "AUTO-DECIDE"
        if rate >= 0.25:
            return "HIGH SWING"
        if rate >= 0.10:
            return "SPICY"
        return "FLAVOR"

    @property
    def note(self) -> str:
        return {
            "LOTTERY TICKET": "rarely fires, decides the game when it does",
            "AUTO-DECIDE": "when it fires, it is the game",
            "HIGH SWING": "noticeable; consider dialling the magnitude down",
            "SPICY": "adds variance without taking over",
            "FLAVOR": "mostly cosmetic",
        }[self.verdict]


@dataclass(frozen=True, slots=True)
class Balance:
    wins_stdev_base: float
    wins_stdev_candidate: float
    underdog_rate_base: Rate
    underdog_rate_candidate: Rate
    skill_correlation_base: float | None
    skill_correlation_candidate: float | None


@dataclass(frozen=True, slots=True)
class Luck:
    points_to_wins_base: float
    points_to_wins_candidate: float
    allplay_to_actual_base: float
    allplay_to_actual_candidate: float


@dataclass(frozen=True, slots=True)
class Analysis:
    league_seasons: int
    matchups: int
    median_margin: float
    overall: RuleImpact
    per_rule: tuple[RuleImpact, ...]
    balance: Balance
    luck: Luck
    by_position: dict[str, float]
    by_team_skill: tuple[tuple[float, float], ...]  # (skill, wins delta)
    comparable_lineups: bool


def _impact(
    name: str,
    diffs: list[MatchupDiff],
    team_weeks: int,
    fired_team_weeks: int,
    swing_of,
) -> RuleImpact:
    swings = [swing_of(d) for d in diffs]
    triggered = [(d, s) for d, s in zip(diffs, swings, strict=True) if abs(s) > NEGLIGIBLE]
    magnitudes = [abs(s) for _, s in triggered]

    decisive = sum(1 for d, s in triggered if abs(s) > abs(d.margin_base))
    flips = sum(
        1 for d, s in zip(diffs, swings, strict=True) if abs(s) > NEGLIGIBLE and _would_flip(d, s)
    )
    return RuleImpact(
        name=name,
        fired=Rate(fired_team_weeks, team_weeks),
        total_points=sum(abs(s) for s in swings),
        mean_when_fired=st.mean(magnitudes) if magnitudes else 0.0,
        decisive=Rate(decisive, len(triggered)),
        flips=Rate(flips, len(diffs)),
        median_swing=st.median(magnitudes) if magnitudes else 0.0,
    )


def _would_flip(diff: MatchupDiff, swing: float) -> bool:
    """Whether this rule alone would have changed the winner."""
    return _sign(diff.margin_base) != _sign(diff.margin_base + swing)


def _all_play(result: SeasonResult) -> dict[str, float]:
    """Win rate if every team played every other team every week.

    Strips the schedule out. When actual results stop tracking all-play, the rule has
    injected luck rather than rewarding scoring.
    """
    wins: dict[str, float] = {team: 0.0 for team in result.settings.teams}
    games: dict[str, int] = {team: 0 for team in result.settings.teams}
    for week in result.weeks:
        scores = [(team, tw.points) for team, tw in week.team_weeks.items()]
        for team, points in scores:
            for other, other_points in scores:
                if team == other:
                    continue
                games[team] += 1
                wins[team] += (
                    1.0 if points > other_points else 0.5 if points == other_points else 0.0
                )
    return {team: wins[team] / games[team] if games[team] else 0.0 for team in wins}


def compare(
    pairs: list[tuple[SeasonResult, SeasonResult]],
    baseline: RuleSet,
    candidate: RuleSet,
    skills: list[dict[str, float]] | None = None,
) -> Analysis:
    """Turn baseline/candidate season pairs into the numbers that answer the question."""
    diffs: list[MatchupDiff] = []
    for base, cand in pairs:
        diffs.extend(diff_season(base, cand))

    # Team-week counts, for how often a rule fires from a manager's point of view.
    team_weeks = sum(len(week.team_weeks) for _, cand in pairs for week in cand.weeks)
    fired_any = 0
    fired_by_rule: dict[str, int] = {}
    points_by_position: dict[str, float] = {}
    for _, cand in pairs:
        for week in cand.weeks:
            for team_week in week.team_weeks.values():
                names = {
                    name
                    for line in team_week.scored
                    for name, delta in line.rule_points.items()
                    if abs(delta) > NEGLIGIBLE
                }
                fired_any += bool(names)
                for name in names:
                    fired_by_rule[name] = fired_by_rule.get(name, 0) + 1
                for line in team_week.scored:
                    for delta in line.rule_points.values():
                        position = line.line.position or "(no production)"
                        points_by_position[position] = points_by_position.get(position, 0.0) + delta

    overall = _impact("all rules", diffs, team_weeks, fired_any, lambda d: d.swing)
    rule_names = sorted({name for d in diffs for name in d.rule_swing})
    per_rule = tuple(
        _impact(
            name,
            diffs,
            team_weeks,
            fired_by_rule.get(name, 0),
            lambda d, n=name: d.rule_swing.get(n, 0.0),
        )
        for name in rule_names
    )

    return Analysis(
        league_seasons=len(pairs),
        matchups=len(diffs),
        median_margin=st.median([abs(d.margin_base) for d in diffs]) if diffs else 0.0,
        overall=overall,
        per_rule=per_rule,
        balance=_balance(pairs, diffs, skills),
        luck=_luck(pairs),
        by_position=points_by_position,
        by_team_skill=_team_effects(pairs, skills),
        comparable_lineups=baseline.lineup.starters == candidate.lineup.starters,
    )


def _balance(pairs, diffs: list[MatchupDiff], skills) -> Balance:
    base_wins, cand_wins = [], []
    skill_pairs_base, skill_pairs_cand, skill_values = [], [], []
    for index, (base, cand) in enumerate(pairs):
        for team in base.settings.teams:
            base_wins.append(base.standings[team].wins)
            cand_wins.append(cand.standings[team].wins)
            if skills:
                skill_values.append(skills[index][team])
                skill_pairs_base.append(base.standings[team].win_pct)
                skill_pairs_cand.append(cand.standings[team].win_pct)

    # Underdog by baseline season scoring, a strength proxy the rule can't move.
    strength = {}
    for base, _ in pairs:
        for team in base.settings.teams:
            strength[(base.key, team)] = base.standings[team].points_for

    def underdogs(use_candidate: bool) -> Rate:
        wins = total = 0
        for diff in diffs:
            home_strength = strength[(diff.league_key, diff.home)]
            away_strength = strength[(diff.league_key, diff.away)]
            if home_strength == away_strength:
                continue
            margin = diff.margin_candidate if use_candidate else diff.margin_base
            if margin == 0:
                continue
            total += 1
            underdog_is_home = home_strength < away_strength
            if (margin > 0) == underdog_is_home:
                wins += 1
        return Rate(wins, total)

    return Balance(
        wins_stdev_base=st.pstdev(base_wins) if len(base_wins) > 1 else 0.0,
        wins_stdev_candidate=st.pstdev(cand_wins) if len(cand_wins) > 1 else 0.0,
        underdog_rate_base=underdogs(False),
        underdog_rate_candidate=underdogs(True),
        skill_correlation_base=spearman(skill_values, skill_pairs_base) if skills else None,
        skill_correlation_candidate=spearman(skill_values, skill_pairs_cand) if skills else None,
    )


def _luck(pairs) -> Luck:
    base_points, base_wins, cand_points, cand_wins = [], [], [], []
    base_allplay, base_actual, cand_allplay, cand_actual = [], [], [], []
    for base, cand in pairs:
        base_ap, cand_ap = _all_play(base), _all_play(cand)
        for team in base.settings.teams:
            base_points.append(base.standings[team].points_for)
            base_wins.append(base.standings[team].win_pct)
            cand_points.append(cand.standings[team].points_for)
            cand_wins.append(cand.standings[team].win_pct)
            base_allplay.append(base_ap[team])
            base_actual.append(base.standings[team].win_pct)
            cand_allplay.append(cand_ap[team])
            cand_actual.append(cand.standings[team].win_pct)

    return Luck(
        points_to_wins_base=spearman(base_points, base_wins),
        points_to_wins_candidate=spearman(cand_points, cand_wins),
        allplay_to_actual_base=spearman(base_allplay, base_actual),
        allplay_to_actual_candidate=spearman(cand_allplay, cand_actual),
    )


def _team_effects(pairs, skills) -> tuple[tuple[float, float], ...]:
    if not skills:
        return ()
    effects = []
    for index, (base, cand) in enumerate(pairs):
        for team in base.settings.teams:
            delta = cand.standings[team].wins - base.standings[team].wins
            effects.append((skills[index][team], float(delta)))
    return tuple(effects)
