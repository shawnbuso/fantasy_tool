"""Source-agnostic data model.

`StatLine` is the contract between data sources (nflreadpy, Yahoo) and everything
downstream. Stats live in a dict rather than named fields because no two sources
supply the same columns: each importer fills what it has and the rest reads as 0.0.
Typo safety comes from validating keys against the stat registry at load time.
"""

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


def zero_line(player_id: str, season: int, week: int) -> StatLine:
    """A blank line, used when a player has no row for a week.

    Byes, injuries, and inactives all show up as simply missing from the source
    data, so returning zeros here removes the need to special-case any of them.
    """
    return StatLine(
        player_id=player_id,
        name=player_id,
        position="",
        nfl_team="",
        season=season,
        week=week,
        opponent="",
        stats={},
    )
