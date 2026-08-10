"""Turning cached Yahoo pages into lineups.

The matchup page carries both teams at once, mirrored around a central slot column:

    Stats | Player | Proj | Fan Pts | Pos ‖ Pos ‖ Pos | Fan Pts | Proj | Player | Stats
      0       1       2        3       4     5     6       7       8       9       10

Two tables per page -- `statTable1` is the starters, `statTable2` the bench.

Players are identified by the Yahoo id in their profile link rather than by name.
Names collide, change, and are rendered with suffixes and injury markers attached; an
id in an href is unambiguous, and nflverse publishes a Yahoo-to-gsis crosswalk so it
maps straight onto the stats everything else is built from.

Nothing here touches the network.
"""

import re
from dataclasses import dataclass
from pathlib import Path

HOME_COLUMNS = {"stats": 0, "player": 1, "projected": 2, "points": 3}
AWAY_COLUMNS = {"stats": 10, "player": 9, "projected": 8, "points": 7}
SLOT_COLUMN = 5

_YAHOO_PLAYER_ID = re.compile(r"/nfl/players/(\d+)")
_RECORD = re.compile(r"^(.*?)([A-Z][a-z]+)?\d+-\d+-\d+")


@dataclass(frozen=True, slots=True)
class Slot:
    """One filled lineup slot."""

    slot: str  # QB, W/R/T, BN ...
    yahoo_id: str | None
    name: str
    points: float | None  # None when the game hasn't been played
    projected: float | None

    @property
    def started(self) -> bool:
        return self.slot != "BN"


@dataclass(frozen=True, slots=True)
class Matchup:
    """One week of one matchup, both sides."""

    week: int
    home_team: str
    away_team: str
    home: tuple[Slot, ...]
    away: tuple[Slot, ...]
    # Yahoo's own total row, which is what makes this parser self-checking.
    reported_home: float | None = None
    reported_away: float | None = None

    def starters(self, away: bool = False) -> tuple[Slot, ...]:
        return tuple(s for s in (self.away if away else self.home) if s.started)

    def total(self, away: bool = False) -> float:
        return sum(s.points or 0.0 for s in self.starters(away))

    def reconciles(self, tolerance: float = 0.05) -> bool:
        """Whether our starter totals match the total Yahoo printed.

        A free and complete check on the column mapping. The page is mirrored around a
        centre column, so an off-by-one would read the opponent's points and still look
        entirely plausible -- until these disagree.
        """
        for reported, mine in (
            (self.reported_home, self.total()),
            (self.reported_away, self.total(away=True)),
        ):
            if reported is not None and abs(reported - mine) > tolerance:
                return False
        return True


def _number(text: str) -> float | None:
    """Yahoo writes an unplayed game as an en dash, not a zero.

    The difference matters: a real zero is a performance, an en dash is an absence,
    and collapsing them would invent production that never happened.
    """
    cleaned = text.strip().replace(",", "")
    if cleaned in {"", "-", "–", "—"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _player(cell) -> tuple[str | None, str]:
    """The Yahoo id and display name from a player cell, or (None, '') if empty."""
    link = cell.css_first("a.name")
    if link is None:
        return None, ""
    href = link.attributes.get("href", "") or ""
    match = _YAHOO_PLAYER_ID.search(href)
    name = (link.attributes.get("title") or link.text(strip=True) or "").strip()
    return (match.group(1) if match else None), name


def _side(cells, columns: dict[str, int], slot: str) -> Slot | None:
    if max(columns.values()) >= len(cells):
        return None
    yahoo_id, name = _player(cells[columns["player"]])
    if not name:
        return None
    return Slot(
        slot=slot,
        yahoo_id=yahoo_id,
        name=name,
        points=_number(cells[columns["points"]].text()),
        projected=_number(cells[columns["projected"]].text()),
    )


def team_names(tree) -> tuple[str, str]:
    """Both managers' team names, from the matchup header.

    Yahoo renders each as the team name, the manager, then the record run together --
    "Bobalki BanditsShawn5-10-0 | 9th" -- so the name is whatever precedes the
    manager and record.
    """
    found: list[str] = []
    for node in tree.css("a, div, span"):
        text = " ".join(node.text(strip=True).split())
        match = _RECORD.match(text)
        if match and match.group(1) and len(match.group(1)) < 40:
            name = match.group(1).strip()
            if name and name not in found:
                found.append(name)
        if len(found) == 2:
            break
    while len(found) < 2:
        found.append(f"Team {len(found) + 1}")
    return found[0], found[1]


def parse_matchup(html: str, week: int) -> Matchup:
    """One cached matchup page into both lineups."""
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    home_slots: list[Slot] = []
    away_slots: list[Slot] = []
    reported_home = reported_away = None

    for table_id in ("statTable1", "statTable2"):
        table = tree.css_first(f"table#{table_id}")
        if table is None:
            continue
        for row in table.css("tr"):
            cells = row.css("td")
            if len(cells) <= SLOT_COLUMN:
                continue  # header row, or a spacer
            slot = cells[SLOT_COLUMN].text(strip=True)
            if not slot:
                continue
            if slot.lower() == "total":
                # Both tables carry a total row; only the starters' one is the score.
                # Taking the last would silently record the bench total instead.
                if table_id == "statTable1":
                    reported_home = _number(cells[HOME_COLUMNS["points"]].text())
                    reported_away = _number(cells[AWAY_COLUMNS["points"]].text())
                continue
            if (side := _side(cells, HOME_COLUMNS, slot)) is not None:
                home_slots.append(side)
            if (side := _side(cells, AWAY_COLUMNS, slot)) is not None:
                away_slots.append(side)

    home_name, away_name = team_names(tree)
    return Matchup(
        week=week,
        home_team=home_name,
        away_team=away_name,
        home=tuple(home_slots),
        away=tuple(away_slots),
        reported_home=reported_home,
        reported_away=reported_away,
    )


def parse_file(path: Path, week: int) -> Matchup:
    return parse_matchup(path.read_text(), week)


def crosswalk() -> dict[str, str]:
    """Yahoo player id to gsis id, so lineups join onto the stats store.

    Built from nflverse's published id table. Coverage is partial -- roughly five
    thousand players carry both ids -- so callers must handle a miss rather than
    assume one.
    """
    import nflreadpy as nfl
    import polars as pl

    ids = nfl.load_ff_playerids().filter(
        pl.col("yahoo_id").is_not_null() & pl.col("gsis_id").is_not_null()
    )
    return {
        str(row["yahoo_id"]): row["gsis_id"]
        for row in ids.select("yahoo_id", "gsis_id").iter_rows(named=True)
    }
