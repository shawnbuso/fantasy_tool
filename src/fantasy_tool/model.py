"""Source-agnostic data model.

`StatLine` is the contract between data sources (nflreadpy, Yahoo) and everything
downstream. Stats live in a dict rather than named fields because no two sources
supply the same columns: each importer fills what it has and the rest reads as 0.0.
Typo safety comes from validating keys against the stat registry at load time.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


@dataclass(frozen=True, slots=True)
class StatLine:
    """One player's (or one team defense's) production in one week."""

    player_id: str  # gsis_id for players; team abbreviation for DEF units
    name: str
    position: str
    nfl_team: str
    season: int
    week: int
    opponent: str
    stats: dict[str, float]
    # Per-event detail that doesn't reduce to a count, e.g. the exact yardage of
    # every made field goal. Lets rules bucket kicks any way they like.
    events: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def s(self, key: str) -> float:
        """Stat value, defaulting to 0.0 for anything this source didn't supply."""
        return self.stats.get(key, 0.0)


SLOT_ELIGIBILITY: dict[str, frozenset[str]] = {
    "QB": frozenset({"QB"}),
    "RB": frozenset({"RB"}),
    "WR": frozenset({"WR"}),
    "TE": frozenset({"TE"}),
    "K": frozenset({"K"}),
    "DEF": frozenset({"DEF"}),
    # Flex slots, spelled the way Yahoo spells them.
    "W/R": frozenset({"WR", "RB"}),
    "W/T": frozenset({"WR", "TE"}),
    "W/R/T": frozenset({"WR", "RB", "TE"}),
    "Q/W/R": frozenset({"QB", "WR", "RB"}),
    "Q/W/R/T": frozenset({"QB", "WR", "RB", "TE"}),  # superflex
}


@dataclass(frozen=True, slots=True)
class LineupSlot:
    label: str
    eligible: frozenset[str]

    @property
    def is_flex(self) -> bool:
        return len(self.eligible) > 1


def parse_slots(labels: Iterable[str]) -> tuple[LineupSlot, ...]:
    slots = []
    for label in labels:
        eligible = SLOT_ELIGIBILITY.get(label.upper())
        if eligible is None:
            known = ", ".join(SLOT_ELIGIBILITY)
            raise ValueError(f"unknown lineup slot {label!r}. Known slots: {known}")
        slots.append(LineupSlot(label.upper(), eligible))
    return tuple(slots)


@dataclass(frozen=True, slots=True)
class LeagueSettings:
    name: str
    season: int
    teams: tuple[str, ...]
    slots: tuple[LineupSlot, ...]
    bench: int
    weeks: tuple[int, ...]

    @property
    def roster_size(self) -> int:
        return len(self.slots) + self.bench


@dataclass(frozen=True, slots=True)
class ScoredLine:
    """A stat line with its points, keeping each custom rule's contribution separate.

    Holding the breakdown rather than one total is what makes the winners-and-losers
    analysis possible: you can ask how many points a rule handed to kickers, or to one
    manager, without re-running anything.
    """

    line: StatLine
    base: float  # from the YAML scoring alone
    rule_points: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return self.base + sum(self.rule_points.values())


@dataclass(frozen=True, slots=True)
class TeamWeek:
    """One fantasy team's starting lineup for one week, scored."""

    team: str
    week: int
    scored: tuple[ScoredLine, ...]

    @property
    def points(self) -> float:
        return sum(s.total for s in self.scored)

    @property
    def base_points(self) -> float:
        return sum(s.base for s in self.scored)

    def at(self, position: str) -> tuple[ScoredLine, ...]:
        return tuple(s for s in self.scored if s.line.position == position)


@dataclass(frozen=True, slots=True)
class Matchup:
    week: int
    home: str
    away: str

    def opponent_of(self, team: str) -> str:
        return self.away if team == self.home else self.home


@dataclass(frozen=True, slots=True)
class Record:
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0
    points_against: float = 0.0

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def win_pct(self) -> float:
        """Ties count as half a win, the usual fantasy convention."""
        return (self.wins + 0.5 * self.ties) / self.games if self.games else 0.0


@dataclass(frozen=True, slots=True)
class WeekResult:
    week: int
    team_weeks: dict[str, TeamWeek]
    matchups: tuple[Matchup, ...]


@dataclass(frozen=True, slots=True)
class History:
    """Weeks that have already finished.

    Rules receive this instead of the whole season, so a rule structurally cannot see
    the future -- there is no way to write one that peeks at a result it shouldn't
    know yet, rather than a convention saying please don't.
    """

    weeks: tuple[WeekResult, ...] = ()

    def record(self, team: str) -> Record:
        wins = losses = ties = 0
        points_for = points_against = 0.0
        for week in self.weeks:
            for matchup in week.matchups:
                if team not in (matchup.home, matchup.away):
                    continue
                mine = week.team_weeks[team].points
                theirs = week.team_weeks[matchup.opponent_of(team)].points
                points_for += mine
                points_against += theirs
                if mine > theirs:
                    wins += 1
                elif mine < theirs:
                    losses += 1
                else:
                    ties += 1
        return Record(wins, losses, ties, points_for, points_against)

    def team_weeks(self, team: str) -> list[TeamWeek]:
        return [w.team_weeks[team] for w in self.weeks if team in w.team_weeks]

    def player_lines(self, player_id: str) -> list[ScoredLine]:
        """Every prior week this player started, oldest first.

        Returns the scored lines rather than bare totals so a rule can choose between
        `.base` and `.total`. Prefer `.base` for anything streak-shaped: keying a
        bonus off `.total` lets the bonus feed its own trigger.
        """
        found = []
        for week in self.weeks:
            for team_week in week.team_weeks.values():
                found.extend(s for s in team_week.scored if s.line.player_id == player_id)
        return found


@dataclass(frozen=True, slots=True)
class League:
    """One league-season: who owns whom, who started, and who played whom.

    Source-agnostic on purpose. The synthetic generator and the Yahoo importer both
    produce exactly this, so everything downstream is indifferent to where it came
    from. `key` identifies the league-season and is what the counterfactual diff joins
    on -- baseline and candidate runs must share it or the comparison is meaningless.
    """

    key: str
    settings: LeagueSettings
    lines: dict[tuple[str, int], StatLine]
    rosters: dict[str, frozenset[str]]
    lineups: dict[tuple[str, int], tuple[str, ...]]
    schedule: tuple[Matchup, ...]
    positions: dict[str, str] = field(default_factory=dict)
    meta: dict[str, object] = field(default_factory=dict)

    def line(self, player_id: str, week: int) -> StatLine:
        """This player's week, or a blank line if he has no row.

        Byes, injuries, and inactives are all simply absent from the source data, so
        zero-filling here means none of them needs a special case anywhere else.

        The blank keeps the player's position. A tight end who didn't produce is still
        a tight end: without this, position-scoped rules silently skip him and the
        per-position breakdown under-counts, both in ways that look like real results.
        """
        found = self.lines.get((player_id, week))
        if found is not None:
            return found
        return zero_line(
            player_id, self.settings.season, week, position=self.positions.get(player_id, "")
        )

    def week_matchups(self, week: int) -> tuple[Matchup, ...]:
        return tuple(m for m in self.schedule if m.week == week)


@dataclass(frozen=True, slots=True)
class SeasonResult:
    key: str
    settings: LeagueSettings
    weeks: tuple[WeekResult, ...]
    standings: dict[str, Record]

    @property
    def history(self) -> History:
        return History(self.weeks)

    def team_week(self, team: str, week: int) -> TeamWeek:
        return next(w.team_weeks[team] for w in self.weeks if w.week == week)


def zero_line(player_id: str, season: int, week: int, position: str = "") -> StatLine:
    """A blank line, used when a player has no row for a week.

    Byes, injuries, and inactives all show up as simply missing from the source
    data, so returning zeros here removes the need to special-case any of them.
    """
    return StatLine(
        player_id=player_id,
        name=player_id,
        position=position,
        nfl_team="",
        season=season,
        week=week,
        opponent="",
        stats={},
    )
