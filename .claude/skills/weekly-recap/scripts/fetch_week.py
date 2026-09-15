"""Pull one week of a Yahoo fantasy league into JSON a recap can be written from.

The repo's own `yahoo-season` deliberately throws Yahoo's points away and recomputes
them from nflverse, because it exists to test rule changes. A recap wants the
opposite: the official numbers the league actually saw, exactly as Yahoo rendered
them. So this reads the one page that carries them.

  /f1/{league}/{team}/team?week=N

That page holds the whole roster -- starters and bench -- with Fan Pts and Proj Pts
per player, plus a card naming the opponent. Ten of those is a full week. The
`/starters` page used elsewhere is cheaper but carries no points at all, and the
`/matchup` page renders the logged-in user's own game whatever team id you ask for.

Positions come from nflverse's id table rather than the page: Yahoo prints "BN" for
every bench player and nothing else, so without a join there is no way to know a
bench player is a running back -- which is the whole input to "points left on the
bench", the single most reliable source of comedy in a recap.
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

from fantasy_tool.model import SLOT_ELIGIBILITY
from fantasy_tool.sources.yahoo.fetch import BASE, Scraper, SessionExpired

BENCH_SLOTS = {"BN", "IR", "IR+"}
# The house participation-trophy rule: an offensive starter who takes at least one
# snap and scores exactly zero is credited with ten points instead. Kickers and
# defenses are excluded -- special teams doesn't count. Yahoo can't express this, so
# the commissioner posts it by hand and only the displayed score reflects it; this is
# here so the recap can name *which* zero earned the ten rather than reporting an
# unexplained delta. A player who never dressed reads as "-" rather than 0.00, so he
# doesn't qualify and doesn't get counted.
TROPHY_POINTS = 10.0
# An injured-reserve player is not startable, so counting him toward the best
# available lineup would invent points nobody could have scored.
UNSTARTABLE = {"IR", "IR+"}
CURRENT_SEASON = 2026
# Yahoo's three roster tables, in page order. The heading names the group, which is
# how a kicker or a defense is identified -- their rows say "BN" on the bench too.
TABLE_GROUPS = {"statTable0": "OFF", "statTable1": "K", "statTable2": "DEF"}

_PLAYER_ID = re.compile(r"/nfl/players/(\d+)")
_MATCHUP = re.compile(r"Week\s*(\d+)\s*vs\s*<a[^>]*/f1/\d+/(\d+)\"?[^>]*>(.*?)</a>", re.DOTALL)
# The team card's two headline figures, each a bold number over a caption.
_HEADLINE = re.compile(r"Fz-xxl\">([^<]+)</span><em[^>]*>(.*?)</em>", re.DOTALL)
# Inside the matchup card, own team first and opponent second. Matched on class
# *tokens* rather than the literal attribute text: once a game is final Yahoo bolds the
# winner's score to "Fz-lg  Fw-b", and a pattern anchored on `Fz-lg'>` silently skips
# every winner and reports the loser's score for both teams -- a whole week of
# zero-margin ties that look almost plausible.
_SCORE_CLASS = "Fz-lg"
_PROJ_CLASS = "proj-pts-matchup"


def _number(text: str) -> float | None:
    """Yahoo prints '-' for a player who hasn't played, and '24.22' otherwise."""
    cleaned = text.strip().replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_team_page(html: str) -> dict:
    """One team's full roster for one week, with the opponent it faced."""
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    players: list[dict] = []

    for table_id, group in TABLE_GROUPS.items():
        table = tree.css_first(f"#{table_id}")
        if table is None:
            continue
        for row in table.css("tr"):
            cells = row.css("td")
            # Find the points column by its class rather than counting to it. Yahoo
            # added a second action icon between 2025 and 2026, which silently moved
            # Fan Pts from index 4 to 5 -- and index 4 is the *bye week*, so a hard
            # -coded offset doesn't fail loudly, it reports a league where everyone
            # scored a clean 96. The `pts` class has survived both layouts.
            scored = [
                i
                for i, cell in enumerate(cells)
                if "pts" in (cell.attributes.get("class") or "").split()
            ]
            if not scored or scored[0] + 1 >= len(cells):
                continue
            fan, projected = scored[0], scored[0] + 1
            # Same reasoning for the player cell. The logged-in user's *own* team page
            # carries an extra hidden "edit" column, so on exactly one of the ten
            # pages the player sits at index 2 rather than 1 -- which silently parsed
            # the commissioner's own team as having no roster at all.
            named = [
                i
                for i, cell in enumerate(cells)
                if "player" in (cell.attributes.get("class") or "").split()
            ]
            slot = cells[0].text(strip=True)
            link = cells[named[0]].css_first("a.name") if named else None
            if not slot or link is None:
                continue
            href = link.attributes.get("href", "") or ""
            found = _PLAYER_ID.search(href)
            detail = cells[named[0]].css_first(".ysf-player-detail")
            players.append(
                {
                    "slot": slot.upper(),
                    "group": group,
                    "name": (link.attributes.get("title") or link.text(strip=True)).strip(),
                    "yahoo_id": found.group(1) if found else None,
                    "points": _number(cells[fan].text()),
                    "projected": _number(cells[projected].text()),
                    # "Final W 31-0 vs LAR" -- the NFL game behind the fantasy line.
                    "game": " ".join(detail.text(strip=True).split()) if detail else "",
                }
            )

    # The title reads "<league> - <team> | Fantasy Football | Yahoo! Sports". Team
    # team names can themselves contain hyphens, so split on the spaced form.
    title = tree.css_first("title")
    heading = title.text(strip=True).split("|")[0].strip() if title else ""
    _, separator, team_name = heading.partition(" - ")
    team_name = team_name.strip() if separator else heading

    # Record and season points as of this fetch -- run on Monday night and they are
    # the standings the week just produced.
    record = points_for = place = None
    for value, caption in _HEADLINE.findall(html):
        flat = " ".join(re.sub(r"<[^>]+>", " ", caption).split())
        # Week 1 shows a bare "Record" with no standing and no season total yet;
        # from week 2 the same slot reads "3rd Place".
        if "Place" in flat or flat == "Record":
            record = value.strip()
            place = flat if "Place" in flat else None
        elif "Total Points" in flat:
            points_for = _number(value.replace(",", ""))

    # Yahoo's own score for this team, which is not always the sum of its starters:
    # the commissioner can post a manual adjustment, and this league does. Summing
    # the roster gives the raw figure; only the card gives the official one.
    start = html.find("team-card-matchup")
    card = HTMLParser(html[start : start + 4000]) if start != -1 else None

    def _card_values(token: str) -> list[float | None]:
        if card is None:
            return []
        return [
            _number(node.text())
            for node in card.css("div")
            if token in (node.attributes.get("class") or "").split()
        ]

    scores = _card_values(_SCORE_CLASS)
    projections = _card_values(_PROJ_CLASS)

    opponent = None
    found = _MATCHUP.search(html)
    if found:
        opponent = {
            "team_id": int(found.group(2)),
            "name": " ".join(re.sub(r"<[^>]+>", " ", found.group(3)).split()),
        }

    return {
        "team_name": team_name,
        "official": scores[0] if scores else None,
        # The opponent's score as *this* page reports it. Redundant by design: it has
        # to agree with the opponent's own page, and checking that is what catches a
        # card-parsing change before it reaches the recap.
        "opponent_official": scores[1] if len(scores) > 1 else None,
        "live_projection": projections[0] if projections else None,
        "record": record,
        "place": place,
        "points_for": points_for,
        "opponent": opponent,
        "players": players,
    }


# Yahoo prints "Kyle Pitts Sr."; nflverse prints "Kyle Pitts". Dropping the suffix is
# what lets the two join at all.
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def _key(name: str) -> str:
    """A name in a form both sources spell the same way."""
    parts = [re.sub(r"[^a-z]", "", part.lower()) for part in name.split()]
    return "".join(p for p in parts if p and p not in _SUFFIXES)


def positions(season: int) -> tuple[dict[str, str], dict[str, str]]:
    """Positions by Yahoo player id, and by name for the ids that don't join.

    Two sources, because neither alone is enough, and then a name fallback on top of
    both. Rookies carry no `yahoo_id` in *any* nflverse table -- not the published id
    table, which lags a season, nor that season's own roster file -- and rookies are
    exactly who sits on a bench waiting to be the player you should have started. Names
    are ambiguous in general but safe within one season's rosters, so only unique
    ones are kept.
    """
    import nflreadpy as nfl

    by_id: dict[str, str] = {}
    seen: dict[str, set[str]] = {}

    for loader, column in (
        (lambda: nfl.load_ff_playerids(), "name"),
        (lambda: nfl.load_rosters(season), "full_name"),
    ):
        try:
            table = loader()
        except Exception as exc:  # noqa: BLE001 - a missing season file is not fatal
            print(f"  (skipping a position source: {exc})", file=sys.stderr)
            continue
        for row in table.select(column, "yahoo_id", "position").iter_rows(named=True):
            position = row["position"]
            if not position:
                continue
            # The id column is a string in one source and a float in the other, and
            # carries empty strings in both. Normalise to the digits Yahoo puts in
            # its hrefs, or the join silently misses.
            ident = str(row["yahoo_id"] or "").strip().removesuffix(".0")
            if ident.isdigit():
                by_id[ident] = position
            if row[column]:
                seen.setdefault(_key(row[column]), set()).add(position)

    by_name = {name: next(iter(p)) for name, p in seen.items() if len(p) == 1}
    return by_id, by_name


def trophy_detail(season: int, week: int, names: list[str]) -> dict[str, dict]:
    """What a participation-trophy player actually did to earn nothing.

    "Scored exactly zero" is only half the joke; the other half is how much football
    he played while doing it. Snap counts and targets turn a blank stat line into a
    man who was on the field for 87% of a 59-point win and caught nothing.
    """
    if not names:
        return {}
    import nflreadpy as nfl
    import polars as pl

    wanted = {_key(n): n for n in names}
    detail: dict[str, dict] = {n: {} for n in names}

    try:
        snaps = nfl.load_snap_counts(season).filter(pl.col("week") == week)
        for row in snaps.select("player", "offense_snaps", "offense_pct").iter_rows(named=True):
            name = wanted.get(_key(row["player"] or ""))
            if name:
                detail[name]["offense_snaps"] = int(row["offense_snaps"] or 0)
                detail[name]["offense_pct"] = row["offense_pct"]
    except Exception as exc:  # noqa: BLE001 - snap data lags the box score early in a week
        print(f"  (no snap counts: {exc})", file=sys.stderr)

    try:
        stats = nfl.load_player_stats(season).filter(pl.col("week") == week)
        columns = [c for c in ("targets", "receptions", "carries") if c in stats.columns]
        for row in stats.select("player_display_name", *columns).iter_rows(named=True):
            name = wanted.get(_key(row["player_display_name"] or ""))
            if name:
                for column in columns:
                    detail[name][column] = int(row[column] or 0)
    except Exception as exc:  # noqa: BLE001
        print(f"  (no player stats: {exc})", file=sys.stderr)

    return detail


def resolve_position(player: dict, by_id: dict[str, str], by_name: dict[str, str]) -> str:
    """The position a player is eligible at, for the optimal-lineup solve.

    A started player's slot already says it, except in a flex. Bench players and flex
    starters need the join; kickers and defenses are settled by which table they were
    printed in.
    """
    if player["group"] in ("K", "DEF"):
        return player["group"]
    slot = player["slot"]
    if slot in SLOT_ELIGIBILITY and len(SLOT_ELIGIBILITY[slot]) == 1:
        return next(iter(SLOT_ELIGIBILITY[slot]))
    found = by_id.get(player["yahoo_id"] or "")
    return found or by_name.get(_key(player["name"]), "")


def bench_swings(players: list[dict], slots: list[str]) -> list[dict]:
    """What each bench player would actually have added, not what he scored.

    A 40-point bench player is not 40 points left behind: starting him means sitting
    somebody, so the real cost is 40 minus whoever he'd have replaced. Quoting the raw
    bench score as the loss overstates it every time, and by a lot -- it is the single
    easiest way to write a sentence that is wrong while every number in it is right.

    Returns the best single swap available for each bench player: his points, the
    cheapest starter he was eligible to replace, and the difference.
    """
    swings: list[dict] = []
    started = [
        {**p, "slot": slot}
        for p, slot in zip(
            [p for p in players if p["slot"] not in BENCH_SLOTS], slots, strict=False
        )
    ]
    for player in players:
        if player["slot"] not in BENCH_SLOTS or player["points"] is None:
            continue
        if player["slot"] in UNSTARTABLE:
            continue
        # Every starting slot this player was eligible to fill.
        replaceable = [
            s
            for s in started
            if player["position"] in SLOT_ELIGIBILITY.get(s["slot"], frozenset())
            and s["points"] is not None
        ]
        if not replaceable:
            continue
        worst = min(replaceable, key=lambda s: s["points"])
        gain = round(player["points"] - worst["points"], 2)
        if gain > 0:
            swings.append(
                {
                    "name": player["name"],
                    "points": player["points"],
                    "instead_of": worst["name"],
                    "their_points": worst["points"],
                    "slot": worst["slot"],
                    "net_gain": gain,
                }
            )
    return sorted(swings, key=lambda s: -s["net_gain"])


def optimal_lineup(players: list[dict], slots: list[str]) -> tuple[float, list[dict]]:
    """The best legal lineup available, and who should have been in it.

    Greedy by how restrictive each slot is. That is genuinely optimal here and not
    just an approximation, because a flex is a *superset* of the dedicated positions:
    filling the singletons first can never strand a player the flex needed, since the
    flex can take whoever the singleton left behind.
    """
    available = [p for p in players if p["points"] is not None and p["slot"] not in UNSTARTABLE]
    order = sorted(range(len(slots)), key=lambda i: len(SLOT_ELIGIBILITY.get(slots[i], ())))
    used: set[int] = set()
    chosen: list[dict] = []

    for index in order:
        eligible = SLOT_ELIGIBILITY.get(slots[index], frozenset())
        best, best_points = None, None
        for position, player in enumerate(available):
            if position in used or player["position"] not in eligible:
                continue
            if best_points is None or player["points"] > best_points:
                best, best_points = position, player["points"]
        if best is not None:
            used.add(best)
            chosen.append({"slot": slots[index], **available[best]})

    return round(sum(p["points"] for p in chosen), 2), chosen


def current_week(scraper, league: str) -> int:
    """Which week the league is on, according to Yahoo.

    A team page with no `week` parameter renders the current one, and its matchup card
    says so in words. Cheaper and more honest than counting Thursdays since kickoff.
    """
    page = scraper.get(f"{BASE}/f1/{league}/1/team", key="current-week", refresh=True)
    found = _MATCHUP.search(page.html)
    if not found:
        raise RuntimeError("could not read the current week from Yahoo; pass --week")
    return int(found.group(1))


def build(
    league: str,
    week: int | None,
    season: int | None,
    teams: int,
    state: Path,
    cache: Path,
    refresh: bool | None,
) -> dict:
    label = season or "current"
    # A live week's scores move all afternoon, so a cached page is stale by
    # construction. Only a finished past season is safe to read from disk by default.
    if refresh is None:
        refresh = season is None
    by_id, by_name = positions(season or CURRENT_SEASON)
    parsed: dict[int, dict] = {}

    with Scraper(state, cache) as scraper:
        if week is None:
            week = current_week(scraper, league)
            print(f"  Yahoo says this is week {week}", file=sys.stderr)
        for team_id in range(1, teams + 1):
            prefix = f"/{season}" if season else ""
            url = f"{BASE}{prefix}/f1/{league}/{team_id}/team?week={week}"
            print(f"  team {team_id}...", file=sys.stderr)
            page = scraper.get(
                url, key=f"{label}/roster-w{week:02d}-t{team_id:02d}", refresh=refresh
            )
            parsed[team_id] = parse_team_page(page.html)

    # Every team runs the same lineup, so one team's starting slots define the league's.
    first = parsed[min(parsed)]
    slots = [p["slot"] for p in first["players"] if p["slot"] not in BENCH_SLOTS]

    records: dict[int, dict] = {}
    for team_id, team in parsed.items():
        for player in team["players"]:
            player["position"] = resolve_position(player, by_id, by_name)
        started = [p for p in team["players"] if p["slot"] not in BENCH_SLOTS]
        bench = [p for p in team["players"] if p["slot"] in BENCH_SLOTS]
        raw = round(sum(p["points"] or 0.0 for p in started), 2)
        # The official score wins where the two disagree -- it is what decided the
        # game. The gap is the commissioner's adjustment, and worth naming out loud.
        actual = team["official"] if team["official"] is not None else raw
        adjustment = round(actual - raw, 2)
        trophies = [p["name"] for p in started if p["group"] == "OFF" and p["points"] == 0.0]
        best, ideal = optimal_lineup(team["players"], slots)
        # A game still being played, rather than a starter who scored nothing.
        live = [
            p["name"] for p in started if p["game"] and not p["game"].startswith(("Final", "Bye"))
        ]

        records[team_id] = {
            "team_id": team_id,
            "team_name": team["team_name"],
            "record": team["record"],
            "place": team["place"],
            "season_points": team["points_for"],
            "opponent_id": team["opponent"]["team_id"] if team["opponent"] else None,
            "opponent_name": team["opponent"]["name"] if team["opponent"] else None,
            "points": actual,
            "opponent_reported": team["opponent_official"],
            "raw_points": raw,
            "adjustment": adjustment,
            "participation_trophy": trophies,
            # If these disagree, either the rule was applied differently this week or
            # the parse is wrong. Either way the recap should not guess.
            "adjustment_explained": abs(adjustment - TROPHY_POINTS * len(trophies)) < 0.01,
            "projected": round(sum(p["projected"] or 0.0 for p in started), 2),
            "live_projection": team["live_projection"],
            "optimal": best,
            "left_on_bench": round(best - raw, 2),
            "still_playing": live,
            "unplayed": [p["name"] for p in started if p["points"] is None],
            "starters": started,
            "bench": bench,
            # Per-player net gain, so a recap can say what a swap was worth rather
            # than quoting a bench score as though it were free.
            "bench_swings": bench_swings(team["players"], slots),
            "should_have_started": [
                {"slot": p["slot"], "name": p["name"], "points": p["points"]}
                for p in ideal
                if p["name"] not in {s["name"] for s in started}
            ],
        }

    # All-play: what this team's record would be against every other team this week.
    # A fresh angle every week that a head-to-head result can't give you -- it
    # separates "played badly" from "ran into the week's high score", and it keeps
    # generating material in November when the jokes about draft day have worn out.
    ranked = sorted(records.values(), key=lambda r: -r["points"])
    for position, team in enumerate(ranked):
        team["all_play"] = f"{len(ranked) - 1 - position}-{position}"
        team["scoring_rank"] = position + 1
        # How much of what they owned they actually started.
        team["efficiency"] = (
            round(100 * team["raw_points"] / team["optimal"], 1) if team["optimal"] else None
        )

    seen: set[int] = set()
    matchups = []
    for team_id in sorted(records):
        opponent = records[team_id]["opponent_id"]
        if team_id in seen or opponent is None or opponent not in records:
            continue
        seen.update({team_id, opponent})
        home, away = records[team_id], records[opponent]
        # Each page reports both scores, so the two must agree. If they don't, the card
        # layout has changed and every number below is suspect -- say so rather than
        # publishing a plausible-looking fiction.
        for one, other in ((home, away), (away, home)):
            claimed = one["opponent_reported"]
            if claimed is not None and abs(claimed - other["points"]) > 0.01:
                print(
                    f"  WARNING: {one['team_name']}'s page says {other['team_name']} "
                    f"scored {claimed}, but that team's own page says {other['points']}."
                    " The matchup card parse is wrong -- do not trust these scores.",
                    file=sys.stderr,
                )
        winner = home if home["points"] >= away["points"] else away
        loser = away if winner is home else home
        matchups.append(
            {
                "teams": [home, away],
                "margin": round(abs(home["points"] - away["points"]), 2),
                "winner": winner["team_name"],
                "combined": round(home["points"] + away["points"], 2),
                # Losing with a top-three score is bad luck, not bad management, and
                # the two deserve different jokes.
                "unlucky_loser": loser["scoring_rank"] <= len(records) // 2,
                # Did a commissioner adjustment decide this? If so it is the story.
                "decided_by_adjustment": (
                    abs(home["points"] - away["points"])
                    <= abs(home["adjustment"] - away["adjustment"])
                    and (home["adjustment"] or away["adjustment"]) != 0
                ),
            }
        )

    # Enrich the trophies once, for the whole league, rather than per team.
    everyone = [n for r in records.values() for n in r["participation_trophy"]]
    detail = trophy_detail(season or CURRENT_SEASON, week, everyone)
    for record in records.values():
        record["participation_trophy"] = [
            {"name": n, **detail.get(n, {})} for n in record["participation_trophy"]
        ]

    scores = sorted(records.values(), key=lambda r: -r["points"])
    return {
        "league_id": league,
        "season": season,
        "week": week,
        "slots": slots,
        "matchups": matchups,
        "week_high": {"team": scores[0]["team_name"], "points": scores[0]["points"]},
        "week_low": {"team": scores[-1]["team_name"], "points": scores[-1]["points"]},
        "league_average": round(sum(r["points"] for r in records.values()) / len(records), 2),
        "teams": list(records.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", required=True, help="Yahoo league id")
    parser.add_argument("--week", type=int, help="Defaults to whatever week Yahoo is on")
    parser.add_argument("--season", type=int, help="Past season; omit for the current one")
    parser.add_argument("--teams", type=int, default=10)
    parser.add_argument("--state", type=Path, default=REPO / "data/yahoo/session.json")
    parser.add_argument("--cache", type=Path, default=REPO / "data/yahoo/pages")
    parser.add_argument(
        "--refresh",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Re-fetch rather than read the cache. On by default for the live season.",
    )
    parser.add_argument("--out", type=Path, help="Write JSON here instead of stdout")
    args = parser.parse_args()

    try:
        data = build(
            args.league, args.week, args.season, args.teams, args.state, args.cache, args.refresh
        )
    except SessionExpired as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("run: uv run fantasy-tool yahoo-auth", file=sys.stderr)
        raise SystemExit(1) from exc

    text = json.dumps(data, indent=2)
    if args.out:
        args.out.write_text(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
