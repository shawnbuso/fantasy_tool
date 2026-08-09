"""Season replay.

Scoring runs in two passes per week. The first scores every starter in the league
under the YAML rules alone; the second runs the custom rules, which by then can see
base-scored teammates and opponents. That ordering is the whole trick: without it a
rule couldn't ask about the rest of the lineup without depending on evaluation order.

Cost is roughly a thousand rule evaluations per league-season, so this is plain
Python dict arithmetic and stays that way. Not optimising it is a decision, not an
oversight -- there is no scale here worth the complexity.
"""

from .model import (
    History,
    League,
    Record,
    ScoredLine,
    SeasonResult,
    TeamWeek,
    WeekResult,
)
from .rules import RuleContext, evaluate
from .scoring import RuleSet, score_base


def _base_week(league: League, week: int, rules: RuleSet) -> dict[str, TeamWeek]:
    """Pass one: every team's starters, scored under the YAML rules only."""
    return {
        team: TeamWeek(
            team=team,
            week=week,
            scored=tuple(
                ScoredLine(line=line, base=score_base(line, rules))
                for line in (
                    league.line(player_id, week) for player_id in league.lineups[(team, week)]
                )
            ),
        )
        for team in league.settings.teams
    }


def _apply_rules(
    team: str,
    opponent: str,
    base: dict[str, TeamWeek],
    history: History,
    rules: RuleSet,
) -> TeamWeek:
    """Pass two: custom rules, with the rest of the week's lineups available."""
    enabled = rules.custom_rules.enabled
    if not enabled:
        return base[team]

    scored = []
    for line in base[team].scored:
        context = RuleContext(
            line=line.line,
            base=line.base,
            params={},
            team=base[team],
            opponent=base[opponent],
            history=history,
        )
        deltas = evaluate(context, enabled)
        scored.append(ScoredLine(line.line, line.base, deltas) if deltas else line)

    return TeamWeek(team=team, week=base[team].week, scored=tuple(scored))


def simulate(league: League, rules: RuleSet) -> SeasonResult:
    """Replay a league-season under one rule set."""
    weeks: list[WeekResult] = []

    for week in league.settings.weeks:
        base = _base_week(league, week, rules)
        matchups = league.week_matchups(week)
        history = History(tuple(weeks))

        final: dict[str, TeamWeek] = {}
        for matchup in matchups:
            for team, opponent in ((matchup.home, matchup.away), (matchup.away, matchup.home)):
                final[team] = _apply_rules(team, opponent, base, history, rules)

        # A team on a bye still gets scored, so nothing silently vanishes from the
        # winners-and-losers breakdown.
        for team, team_week in base.items():
            final.setdefault(team, team_week)

        weeks.append(WeekResult(week=week, team_weeks=final, matchups=matchups))

    completed = History(tuple(weeks))
    standings = {team: completed.record(team) for team in league.settings.teams}
    return SeasonResult(league.key, league.settings, tuple(weeks), standings)


def standings_table(result: SeasonResult) -> list[tuple[str, Record]]:
    """Teams ordered as a league table: record first, then points scored."""
    return sorted(
        result.standings.items(),
        key=lambda pair: (pair[1].win_pct, pair[1].points_for),
        reverse=True,
    )
