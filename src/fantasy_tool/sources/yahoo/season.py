"""Assembling a real league-season out of cached Yahoo pages.

Yahoo's matchup page is *personal*: whatever team id you put in the URL, it renders
the logged-in user's own matchup. So a league-season is built from two other pages:

  /starters?week=N   every team's full lineup for that week, in one page
  /{team}/team?week=N   one team's page, which names the opponent it faced

That makes a season 14 lineup pages plus one pass over the teams to recover the
schedule -- a few dozen requests rather than a few hundred.

Points are deliberately not taken from Yahoo. They're recomputed from nflverse under
whichever rules are being tested, which is the entire point: the league's own scoring
changed from year to year, and replaying it means scoring what the players did, not
what that season's settings happened to pay.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from ...model import LeagueSettings, Matchup, StatLine, parse_slots
from ...store import DEFAULT_ROOT, load_statlines
from .fetch import BASE, Scraper
from .parse import crosswalk

_PLAYER_ID = re.compile(r"/nfl/players/(\d+)")
_OPPONENT = re.compile(r"Week\s*(\d+)\s*vs\.?\s*(.+?)\s*(?:•|<)", re.DOTALL)
BENCH = "BN"


@dataclass(frozen=True, slots=True)
class TeamWeekLineup:
    team_id: int
    team_name: str
    slots: tuple[tuple[str, str | None, str], ...]  # (slot, yahoo_id, name)

    @property
    def started(self) -> tuple[tuple[str, str | None, str], ...]:
        return tuple(row for row in self.slots if row[0] != BENCH)


def starters_url(league_id: str, week: int, season: int | None) -> str:
    prefix = f"/{season}" if season else ""
    return f"{BASE}{prefix}/f1/{league_id}/starters?week={week}"


def team_url(league_id: str, team_id: int, week: int, season: int | None) -> str:
    prefix = f"/{season}" if season else ""
    return f"{BASE}{prefix}/f1/{league_id}/{team_id}/team?week={week}"


def parse_starters(html: str) -> list[TeamWeekLineup]:
    """Every team's lineup from one `/starters` page.

    Each team gets its own `Tst-team-{id}` table, with the slot in the first column
    and the player in the second.
    """
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    lineups: list[TeamWeekLineup] = []

    for table in tree.css("table[id^='Tst-team-']"):
        table_id = table.attributes.get("id", "")
        team_id = int(table_id.rsplit("-", 1)[-1])

        rows: list[tuple[str, str | None, str]] = []
        for row in table.css("tr"):
            cells = row.css("td")
            if len(cells) < 2:
                continue
            slot = cells[0].text(strip=True)
            link = cells[1].css_first("a.name")
            if not slot or link is None:
                continue
            href = link.attributes.get("href", "") or ""
            found = _PLAYER_ID.search(href)
            name = (link.attributes.get("title") or link.text(strip=True) or "").strip()
            rows.append((slot, found.group(1) if found else None, name))

        # The team's name sits just above its table.
        parent = table.parent
        heading = " ".join(parent.text(strip=True).split()) if parent else ""
        name = heading.split("Pos")[0].strip() if "Pos" in heading else f"Team {team_id}"
        lineups.append(TeamWeekLineup(team_id, name or f"Team {team_id}", tuple(rows)))

    return lineups


def parse_opponent(html: str) -> tuple[int, str] | None:
    """The team id and name of the opponent named on a team page."""
    found = _OPPONENT.search(html)
    if not found:
        return None
    name = " ".join(re.sub(r"<[^>]+>", " ", found.group(2)).split())
    link = re.search(r"/f1/\d+/(\d+)\"[^>]*>\s*" + re.escape(name[:20]), html)
    if link is None:
        link = re.search(r"Week\s*\d+\s*vs.{0,200}?/f1/\d+/(\d+)\"", html, re.DOTALL)
    return (int(link.group(1)), name) if link else None


def fetch_season(
    scraper: Scraper,
    league_id: str,
    season: int | None,
    weeks: range,
    teams: int = 10,
    progress=None,
) -> tuple[dict[int, list[TeamWeekLineup]], tuple[Matchup, ...], dict[int, str]]:
    """Lineups for every week, plus the schedule, from cached or live pages."""
    label = season or "current"
    by_week: dict[int, list[TeamWeekLineup]] = {}
    names: dict[int, str] = {}

    for week in weeks:
        if progress:
            progress(f"{label}: lineups for week {week}...")
        page = scraper.get(
            starters_url(league_id, week, season), key=f"{label}/starters-w{week:02d}"
        )
        lineups = parse_starters(page.html)
        by_week[week] = lineups
        for lineup in lineups:
            names.setdefault(lineup.team_id, lineup.team_name)

    schedule: list[Matchup] = []
    for week in weeks:
        paired: set[int] = set()
        for team_id in sorted(names):
            if team_id in paired:
                continue
            if progress:
                progress(f"{label}: week {week} schedule, team {team_id}...")
            page = scraper.get(
                team_url(league_id, team_id, week, season),
                key=f"{label}/sched-w{week:02d}-t{team_id:02d}",
            )
            found = parse_opponent(page.html)
            if found is None:
                continue
            opponent_id, _ = found
            paired.update({team_id, opponent_id})
            schedule.append(
                Matchup(week=week, home=names[team_id], away=names.get(opponent_id, ""))
            )

    return by_week, tuple(schedule), names


def defense_crosswalk() -> dict[str, list[str]]:
    """Yahoo's defense names to team abbreviations.

    Team defenses have no Yahoo player id in the sense the player crosswalk covers,
    and the store keys them by abbreviation. Yahoo names them by nickname -- "Ravens",
    "49ers" -- which nflverse publishes alongside the abbreviation.
    """
    import nflreadpy as nfl

    teams = nfl.load_teams()
    # A nickname can map to several abbreviations, because relocated franchises keep
    # theirs: the Chargers are both SD and LAC, the Raiders both OAK and LV. Return
    # every candidate and let the caller pick the one the season actually used.
    mapping: dict[str, list[str]] = {}
    for row in teams.select("team_nick", "team_abbr").iter_rows(named=True):
        if row["team_nick"] and row["team_abbr"]:
            mapping.setdefault(row["team_nick"], []).append(row["team_abbr"])
    return mapping


def build_league(
    key: str,
    season: int,
    by_week: dict[int, list[TeamWeekLineup]],
    schedule: tuple[Matchup, ...],
    names: dict[int, str],
    root: Path = DEFAULT_ROOT,
) -> tuple[object, dict[str, int]]:
    """Turn parsed pages into a League the simulator can replay.

    Returns the league and a count of how many players failed to cross-reference.
    Unmatched starters are reported rather than silently zeroed: a starter scoring
    nothing because his id didn't resolve looks exactly like one who had a bad week,
    and would quietly corrupt a season of results.
    """
    from ...model import League

    lines: dict[tuple[str, int], StatLine] = load_statlines(season, root=root)
    ids = crosswalk()
    defenses = defense_crosswalk()
    known = {player_id for player_id, _ in lines}
    positions = {line.player_id: line.position for line in lines.values()}
    # Last resort for players the published id table misses -- usually a kicker signed
    # mid-season. Names are ambiguous in general, but within one season's stat lines
    # a unique full-name match is safe, and recovering a real starter beats scoring
    # him zero.
    by_name: dict[str, list[str]] = {}
    for line in lines.values():
        by_name.setdefault(line.name, []).append(line.player_id)
    unique_names = {name: ids[0] for name, ids in by_name.items() if len(set(ids)) == 1}

    weeks = tuple(sorted(by_week))
    slot_labels = [row[0] for row in by_week[weeks[0]][0].started]

    lineups: dict[tuple[str, int], tuple[str, ...]] = {}
    rosters: dict[str, set[str]] = {name: set() for name in names.values()}
    unmatched: dict[str, int] = {}

    for week, team_lineups in by_week.items():
        for lineup in team_lineups:
            started: list[str] = []
            for slot, yahoo_id, name in lineup.slots:
                # "Seahawks - DEF" renders as just the nickname in the starters table.
                nickname = name.split(" - ")[0].strip()
                gsis = ids.get(yahoo_id or "")
                if gsis is None or gsis not in known:
                    candidates = defenses.get(nickname, [])
                    gsis = next((c for c in candidates if c in known), None)
                if gsis is None or gsis not in known:
                    gsis = unique_names.get(nickname)
                if gsis is None or gsis not in known:
                    unmatched[name] = unmatched.get(name, 0) + 1
                    continue
                rosters[lineup.team_name].add(gsis)
                if slot != BENCH:
                    started.append(gsis)
            lineups[(lineup.team_name, week)] = tuple(started)

    settings = LeagueSettings(
        name=f"{key} {season}",
        season=season,
        teams=tuple(names[i] for i in sorted(names)),
        slots=parse_slots(slot_labels),
        bench=len(by_week[weeks[0]][0].slots) - len(slot_labels),
        weeks=weeks,
    )
    league = League(
        key=f"yahoo:{season}",
        settings=settings,
        lines=lines,
        rosters={team: frozenset(players) for team, players in rosters.items()},
        lineups=lineups,
        schedule=schedule,
        positions=positions,
        meta={"source": "yahoo", "season": season},
    )
    return league, unmatched
