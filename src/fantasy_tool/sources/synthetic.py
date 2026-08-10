"""Generate plausible leagues from real NFL seasons.

Real league history is thin -- a 10-team season is about 65 matchups, so a rule that
fires 6% of the time gives four events. Synthetic leagues over the same real player
data give the volume needed to say anything with confidence.

Two modelling choices matter more than the rest:

Draft value comes from the *prior* season, never the current one. With hindsight every
manager drafts perfectly, the spread between teams collapses, and the parity and luck
measurements have nothing left to measure.

Manager skill is an explicit per-team number. It drives draft noise, lineup noise, and
how readily a manager churns the waiver wire -- so it is the shark-versus-casual axis
the league actually cares about, and it is ground truth the analysis can correlate
against.
"""

import random
from dataclasses import dataclass
from statistics import pstdev

from ..model import (
    League,
    LeagueSettings,
    Matchup,
    StatLine,
    parse_slots,
)
from ..scoring import RuleSet, score_standalone
from ..store import DEFAULT_ROOT, load_statlines

DEFAULT_TEAMS = 10
DEFAULT_WEEKS = tuple(range(1, 15))  # a 14-week fantasy regular season

# How many games before a player's current-season form outweighs his prior-season
# value. Low enough to respond, high enough not to overreact to one big week.
SHRINK_GAMES = 4.0

# How badly a zero-skill manager misjudges, as a multiple of the spread in the thing
# being judged. Expressed relative to the data rather than in points, because the
# points scale moves with the rules: switching to full PPR or adding a superflex
# changes what a point is worth, and fixed sigmas would silently go out of
# calibration. Getting this wrong is not subtle -- noise a few times the signal makes
# the draft effectively random, which produces rosters that can't score.
DRAFT_NOISE_SCALE = 1.1
LINEUP_NOISE_SCALE = 0.8

# Streaming friction. A manager needs the replacement to beat the incumbent by this
# much before bothering to make the move, and a hot incumbent is stickier still.
STREAM_SWITCH_COST = 6.0
STREAM_FORM_WEIGHT = 0.6
STREAM_FORM_WEEKS = 3

STREAMED_POSITIONS = ("K", "DEF")

SCORING_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

# How deep the draftable pool runs, as a multiple of what the league needs. A real
# draft board is a couple of hundred names, not everyone who ever took a snap; without
# this, teams fill their benches with players who never see the field and then start
# them, which drags scores down and widens margins.
POOL_DEPTH = 2.5


@dataclass(frozen=True, slots=True)
class RosterPlan:
    """How many of each position a roster wants, derived from the lineup.

    Hardcoding this would silently break the moment the lineup changes, which is the
    whole point of the tool. A superflex league wants five quarterbacks where a
    standard one wants two, and a draft capped at two would simply fail to fill the
    lineup while looking like it worked.
    """

    demand: dict[str, float]  # expected starters per team, for replacement level
    floor: dict[str, int]  # must draft at least this many
    cap: dict[str, int]  # no point drafting more


def lineup_shape(slots) -> tuple[dict[str, int], list]:
    """Split a lineup into dedicated slots per position and the flex slots."""
    dedicated: dict[str, int] = {}
    flex = []
    for slot in slots:
        if slot.is_flex:
            flex.append(slot)
        else:
            position = next(iter(slot.eligible))
            dedicated[position] = dedicated.get(position, 0) + 1
    return dedicated, flex


def _flex_demand(
    values: dict[str, list[float]], dedicated: dict[str, int], flex, teams: int
) -> dict[str, float]:
    """How the flex slots actually get divided up, by value rather than by assumption.

    Assuming each eligible position wins an equal share would be describing the goal
    of a balanced league rather than the league in front of us. Under ordinary scoring
    quarterbacks win superflex slots outright, and pretending otherwise puts
    replacement level for the other positions far too deep -- which inflates their
    value over replacement and drafts rosters carrying six tight ends.
    """
    # Sorted, not a bare set: string hashing is randomised per process, so
    # iterating a set of positions gives a different order every run and the
    # draft board ends up ordered differently. Same seed, different league.
    eligible = sorted({position for slot in flex for position in slot.eligible})
    contenders: list[tuple[float, str]] = []
    for position in eligible:
        ranked = sorted(values.get(position, []), reverse=True)
        # The dedicated starters are already spoken for; the flex picks from the rest.
        contenders.extend(
            (value, position) for value in ranked[teams * dedicated.get(position, 0) :]
        )

    contenders.sort(reverse=True)
    won: dict[str, int] = {}
    for _, position in contenders[: teams * len(flex)]:
        won[position] = won.get(position, 0) + 1
    return {position: won.get(position, 0) / teams for position in eligible}


def roster_plan(slots, values: dict[str, list[float]] | None = None, teams: int = DEFAULT_TEAMS):
    """How many of each position to draft, given the lineup and what players are worth."""
    dedicated, flex = lineup_shape(slots)
    positions = sorted(set(dedicated) | {p for slot in flex for p in slot.eligible})

    if values:
        share = _flex_demand(values, dedicated, flex, teams)
    else:
        # No values to go on: fall back to an equal split of the flex slots.
        share = {
            position: sum(1 / len(slot.eligible) for slot in flex if position in slot.eligible)
            for position in positions
        }

    demand, floor, cap = {}, {}, {}
    for position in positions:
        fixed = dedicated.get(position, 0)
        eligible_flex = [slot for slot in flex if position in slot.eligible]
        demand[position] = fixed + share.get(position, 0.0)

        if position in STREAMED_POSITIONS:
            # Streamed weekly, so a second one would just sit on the bench.
            floor[position] = cap[position] = fixed
        else:
            # A backup at every dedicated position, or a bye empties the slot.
            floor[position] = fixed + 1
            reachable = fixed + len(eligible_flex) + (2 if eligible_flex else 0)
            cap[position] = max(floor[position], reachable)
    return RosterPlan(demand, floor, cap)


@dataclass(frozen=True, slots=True)
class Player:
    player_id: str
    name: str
    position: str
    value: float  # prior-season points per game, under the valuation rules


@dataclass(frozen=True, slots=True)
class Pool:
    """Everything about a season that every league generated from it can share.

    Building this is the expensive part -- loading two seasons and scoring every
    line -- so a sweep builds it once and generates many leagues against it.
    """

    season: int
    players: tuple[Player, ...]
    lines: dict[tuple[str, int], StatLine]
    points: dict[tuple[str, int], float]
    replacement: dict[str, float]
    # (nfl_team, week) pairs where that team didn't play. Managers know these in
    # advance -- byes are on the schedule before the season starts.
    byes: frozenset[tuple[str, int]]
    player_team: dict[str, str]
    # Spread of draft value and of weekly production, used to scale manager error.
    value_spread: float
    weekly_spread: float
    plan: RosterPlan

    def by_position(self, position: str) -> tuple[Player, ...]:
        return tuple(p for p in self.players if p.position == position)

    def on_bye(self, player_id: str, week: int) -> bool:
        team = self.player_team.get(player_id)
        return team is not None and (team, week) in self.byes


def _season_points(lines: dict[tuple[str, int], StatLine], rules: RuleSet) -> dict:
    """Value every line the way a manager would: on the player's own merits.

    Custom rules count here, or managers would draft and start players in ignorance of
    scoring their own league uses.
    """
    return {key: score_standalone(line, rules) for key, line in lines.items()}


def build_pool(
    season: int,
    rules: RuleSet,
    root=DEFAULT_ROOT,
    teams: int = DEFAULT_TEAMS,
) -> Pool:
    """Assemble the draftable player pool for a season.

    Eligibility is deliberately generous: anyone who appears in the season, plus
    anyone who had a real prior season but never appears in this one. That second
    group is what makes busts possible -- a player drafted on last year's form who
    tears an ACL in camp is a dead roster spot, and leagues turn on those.
    """
    lines = load_statlines(season, root=root)
    prior_lines = load_statlines(season - 1, root=root)
    points = _season_points(lines, rules)
    prior_points = _season_points(prior_lines, rules)

    prior_games: dict[str, list[float]] = {}
    prior_meta: dict[str, StatLine] = {}
    for (player_id, _), line in prior_lines.items():
        prior_games.setdefault(player_id, []).append(prior_points[(player_id, line.week)])
        prior_meta.setdefault(player_id, line)

    current_ids = {player_id for player_id, _ in lines}
    eligible = current_ids | {p for p, g in prior_games.items() if len(g) >= 4}

    players = []
    for player_id in sorted(eligible):
        meta = next(
            (lines[(player_id, w)] for w in range(1, 25) if (player_id, w) in lines),
            prior_meta.get(player_id),
        )
        if meta is None or not meta.position:
            continue
        games = prior_games.get(player_id, [])
        value = sum(games) / len(games) if games else 0.0
        players.append(Player(player_id, meta.name, meta.position, value))

    # How the lineup divides up, measured against what players are actually worth
    # under these rules rather than assumed.
    by_position: dict[str, list[float]] = {}
    for player in players:
        if player.value > 0:
            by_position.setdefault(player.position, []).append(player.value)
    plan = roster_plan(parse_slots(rules.lineup.starters), by_position, teams)

    # Replacement level: the value of the best player at a position nobody would
    # start, i.e. just past what the league collectively needs.
    replacement = {}
    for position, demand in plan.demand.items():
        ranked = sorted(
            (p.value for p in players if p.position == position and p.value > 0),
            reverse=True,
        )
        index = min(len(ranked) - 1, int(teams * demand)) if ranked else -1
        replacement[position] = ranked[index] if index >= 0 else 0.0

    # Rookies and anyone without prior data start at replacement level rather than
    # zero, so they're drafted late instead of never.
    players = [
        p if p.value > 0 else Player(p.player_id, p.name, p.position, replacement[p.position])
        for p in players
        if p.position in plan.demand
    ]

    # Trim to a realistic draft board: a couple of hundred names, by position.
    #
    # Membership is by prior-season value OR current-season production; draft *order*
    # still uses prior season alone. That split matters. A board picked on prior value
    # only would exclude every player who breaks out this year, which is precisely the
    # group that makes good lineups -- rosters end up unable to score. Real boards
    # include rookies and breakout candidates; they simply go late, and reward whoever
    # took the flyer. Keeping both directions also preserves both kinds of variance,
    # the bust drafted high on last year's form and the late pick who explodes.
    current_ppg: dict[str, float] = {}
    current_games: dict[str, int] = {}
    for (player_id, week), value in points.items():
        current_ppg[player_id] = current_ppg.get(player_id, 0.0) + value
        current_games[player_id] = current_games.get(player_id, 0) + 1
    for player_id, games in current_games.items():
        current_ppg[player_id] /= games

    board: dict[str, Player] = {}
    for position, demand in plan.demand.items():
        at_position = [p for p in players if p.position == position]
        keep = (
            len(at_position) if position in STREAMED_POSITIONS else int(teams * demand * POOL_DEPTH)
        )
        for ranking in (
            lambda p: p.value,
            lambda p: current_ppg.get(p.player_id, 0.0),
        ):
            for player in sorted(at_position, key=ranking, reverse=True)[:keep]:
                board[player.player_id] = player
    players = tuple(board.values())

    # Every NFL team has a row for every week it plays, because a team defense always
    # records something. So the weeks a team is missing are exactly its bye.
    played = {(line.nfl_team, week) for (_, week), line in lines.items() if line.position == "DEF"}
    all_teams = {team for team, _ in played}
    all_weeks = {week for _, week in played}
    byes = frozenset(
        (team, week) for team in all_teams for week in all_weeks if (team, week) not in played
    )

    # A player's NFL team, taken from his earliest appearance. Mid-season trades make
    # this approximate, but it is only used to spot bye weeks.
    player_team: dict[str, str] = {}
    for (player_id, _), line in sorted(lines.items(), key=lambda kv: kv[0][1]):
        player_team.setdefault(player_id, line.nfl_team)

    over_replacement = [p.value - replacement[p.position] for p in players]
    weekly = [points[key] for key in points if points[key]]
    value_spread = pstdev(over_replacement) if len(over_replacement) > 1 else 1.0
    weekly_spread = pstdev(weekly) if len(weekly) > 1 else 1.0

    return Pool(
        season,
        players,
        lines,
        points,
        replacement,
        byes,
        player_team,
        value_spread or 1.0,
        weekly_spread or 1.0,
        plan,
    )


def _round_robin(teams: tuple[str, ...], weeks: tuple[int, ...]) -> tuple[Matchup, ...]:
    """Circle-method schedule, repeating once every team has played every other."""
    rotation = list(teams)
    if len(rotation) % 2:
        rotation.append("")  # odd league: one bye per week

    rounds: list[list[tuple[str, str]]] = []
    half = len(rotation) // 2
    for _ in range(len(rotation) - 1):
        pairs = [(rotation[i], rotation[-1 - i]) for i in range(half)]
        rounds.append([(h, a) for h, a in pairs if h and a])
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]

    schedule = []
    for index, week in enumerate(weeks):
        for home, away in rounds[index % len(rounds)]:
            # Alternate home and away on repeat cycles so the schedule isn't identical.
            flip = (index // len(rounds)) % 2
            schedule.append(Matchup(week, away if flip else home, home if flip else away))
    return tuple(schedule)


def _draft(
    pool: Pool,
    teams: tuple[str, ...],
    settings: LeagueSettings,
    skill: dict[str, float],
    rng: random.Random,
    stack: tuple[str, str] | None = None,
) -> dict[str, frozenset[str]]:
    """Snake draft. Sharper managers take the best player available; casual ones reach.

    `stack` is (team, position): that manager hoards the position, taking it whenever
    one is available and he can still fill his remaining requirements. It exists to
    test whether a rule set can be exploited by loading up on whichever position looks
    underpriced -- the thing a sharp manager tries first.

    Players are ranked by value *over replacement*, not raw points. Under most scoring
    a quarterback outscores everyone -- but you can only start one, and the next
    quarterback on the waiver wire is nearly as good, so those raw points aren't worth
    a pick. Ranking on raw value instead produces rosters carrying three quarterbacks
    and three receivers, which then can't field a legal lineup.
    """
    over_replacement = {
        p.player_id: p.value - pool.replacement.get(p.position, 0.0) for p in pool.players
    }
    available = {p.player_id: p for p in pool.players}
    rosters: dict[str, list[Player]] = {team: [] for team in teams}
    required = pool.plan.floor
    order = list(teams)

    for round_index in range(settings.roster_size):
        picking = order if round_index % 2 == 0 else list(reversed(order))
        for team in picking:
            roster = rosters[team]
            counts: dict[str, int] = {}
            for player in roster:
                counts[player.position] = counts.get(player.position, 0) + 1

            unmet = {
                position: need - counts.get(position, 0)
                for position, need in required.items()
                if counts.get(position, 0) < need
            }
            picks_left = settings.roster_size - len(roster)

            candidates = [
                p
                for p in available.values()
                if counts.get(p.position, 0) < pool.plan.cap.get(p.position, 0)
            ]
            # Once there are only as many picks left as holes to fill, stop browsing.
            if picks_left <= sum(unmet.values()):
                candidates = [p for p in candidates if p.position in unmet]
            if not candidates:
                candidates = list(available.values())

            spread = DRAFT_NOISE_SCALE * pool.value_spread * (1.0 - skill[team])
            hoard = stack[1] if stack and stack[0] == team else None
            preferred = [p for p in candidates if p.position == hoard] if hoard else []
            choice = max(
                preferred or candidates,
                key=lambda p: over_replacement[p.player_id] + rng.gauss(0.0, spread),
            )
            roster.append(choice)
            del available[choice.player_id]

    return {team: frozenset(p.player_id for p in roster) for team, roster in rosters.items()}


class _Projector:
    """A manager's guess at what a player will do this week.

    Prior-season value early, shrinking toward observed form as the season goes on,
    plus noise inversely proportional to skill. Crucially this never consults the
    current week -- a manager sets a lineup without knowing the outcome.
    """

    def __init__(self, pool: Pool, skill: dict[str, float], rng: random.Random) -> None:
        self._pool = pool
        self._skill = skill
        self._rng = rng
        self._value = {p.player_id: p.value for p in pool.players}
        self._played: dict[str, list[float]] = {}

    def observe(self, player_id: str, week: int) -> None:
        """Record what a player did, counting a blank week as a zero.

        The stat feed only carries players who recorded something, so a player who was
        inactive, cut, or simply never touched the ball is absent rather than zero.
        Treating that absence as a zero is what lets a manager notice: a player who
        stops producing sees his projection decay and stops being started. Bye weeks
        are excluded, since missing those says nothing about a player.
        """
        if self._pool.on_bye(player_id, week):
            return
        self._played.setdefault(player_id, []).append(self._pool.points.get((player_id, week), 0.0))

    def expected(self, player_id: str) -> float:
        played = self._played.get(player_id, [])
        prior = self._value.get(player_id, 0.0)
        if not played:
            return prior
        observed = sum(played) / len(played)
        weight = len(played) / (len(played) + SHRINK_GAMES)
        return weight * observed + (1.0 - weight) * prior

    def recent(self, player_id: str) -> float:
        played = self._played.get(player_id, [])[-STREAM_FORM_WEEKS:]
        return sum(played) / len(played) if played else 0.0

    def guess(self, player_id: str, team: str) -> float:
        sigma = LINEUP_NOISE_SCALE * self._pool.weekly_spread * (1.0 - self._skill[team])
        return self.expected(player_id) + self._rng.gauss(0.0, sigma)


def _stream(
    pool: Pool,
    position: str,
    owners: dict[str, str],
    waiver_order: list[str],
    projector: _Projector,
    skill: dict[str, float],
    active: set[str],
) -> None:
    """Swap a team's kicker or defense, but only when it's clearly worth the bother.

    A manager doesn't drop a producing kicker for a marginally better one. The
    incumbent gets credit for recent form on top of a skill-scaled switching cost, so
    casual managers hold and sharks churn. This matters: freely churned kickers would
    let everyone chase a long-field-goal bonus optimally and overstate how exploitable
    such a rule is.
    """
    owned = set(owners.values())
    league_average = sum(projector.expected(p.player_id) for p in pool.by_position(position)) / max(
        1, len(pool.by_position(position))
    )

    for team in waiver_order:
        incumbent = owners.get(team)
        free_agents = [
            p
            for p in pool.by_position(position)
            if p.player_id not in owned and p.player_id in active
        ]
        if not free_agents:
            continue

        best = max(free_agents, key=lambda p: projector.expected(p.player_id))
        # No incumbent, or the incumbent is on a bye: take the best available.
        if incumbent is None or incumbent not in active:
            owned.discard(incumbent)
            owners[team] = best.player_id
            owned.add(best.player_id)
            continue

        form_bonus = STREAM_FORM_WEIGHT * max(0.0, projector.recent(incumbent) - league_average)
        switch_cost = STREAM_SWITCH_COST * (1.0 - skill[team])
        threshold = projector.expected(incumbent) + form_bonus + switch_cost

        if projector.expected(best.player_id) > threshold:
            owned.discard(incumbent)
            owners[team] = best.player_id
            owned.add(best.player_id)


def _fill_lineup(
    settings: LeagueSettings,
    roster: list[str],
    positions: dict[str, str],
    projector: _Projector,
    team: str,
    active: set[str],
) -> tuple[str, ...]:
    """Assign players to slots, most restrictive slot first.

    Filling dedicated slots before flex is what makes greedy correct here: slot
    eligibility is nested, so a flex never wants a player a dedicated slot needed.

    Players who won't take the field are set aside first. Managers know this in
    reality -- byes are on the schedule and inactives are announced before lineups
    lock -- and without it a player who retired or blew out a knee in camp keeps his
    preseason projection all year and is started every week as a dead roster spot.
    """
    startable = [p for p in roster if p in active] or list(roster)
    guesses = {player_id: projector.guess(player_id, team) for player_id in startable}
    remaining = set(startable)
    chosen: list[str] = []

    for slot in sorted(settings.slots, key=lambda s: len(s.eligible)):
        options = [p for p in remaining if positions.get(p) in slot.eligible]
        if not options:
            chosen.append("")  # nobody eligible; scores as a blank line
            continue
        pick = max(options, key=lambda p: guesses[p])
        remaining.discard(pick)
        chosen.append(pick)

    return tuple(chosen)


def generate(
    pool: Pool,
    seed: int,
    rules: RuleSet,
    *,
    n_teams: int = DEFAULT_TEAMS,
    weeks: tuple[int, ...] = DEFAULT_WEEKS,
    skill_range: tuple[float, float] = (0.15, 0.95),
    stack: str | None = None,
) -> League:
    """Build one league-season: draft, schedule, and every week's lineups.

    Determinism is a contract, not a nicety. The counterfactual compares a baseline
    run against a candidate run, so unless the same seed reproduces exactly the same
    league the difference between them isn't the rule. One Random is threaded
    explicitly; nothing here touches the module-level random.
    """
    rng = random.Random(seed)
    teams = tuple(f"Team {i + 1:02d}" for i in range(n_teams))

    low, high = skill_range
    step = (high - low) / max(1, n_teams - 1)
    skill = {team: low + step * i for i, team in enumerate(teams)}

    settings = LeagueSettings(
        name=f"synthetic {pool.season} seed {seed}",
        season=pool.season,
        teams=teams,
        slots=parse_slots(rules.lineup.starters),
        bench=rules.lineup.bench,
        weeks=weeks,
    )

    # The sharpest manager is the one who would spot and exploit a mispricing.
    hoarder = (max(teams, key=lambda t: skill[t]), stack) if stack else None
    rosters = _draft(pool, teams, settings, skill, rng, hoarder)
    schedule = _round_robin(teams, weeks)
    positions = {p.player_id: p.position for p in pool.players}

    projector = _Projector(pool, skill, rng)
    waiver_order = list(teams)
    rng.shuffle(waiver_order)

    # Kickers and defenses are streamed weekly, so they leave the drafted roster and
    # are tracked separately from here on.
    owners = {
        position: {
            team: next(
                (p for p in rosters[team] if positions.get(p) == position),
                None,
            )
            for team in teams
        }
        for position in STREAMED_POSITIONS
    }
    held = {
        team: [p for p in sorted(rosters[team]) if positions.get(p) not in STREAMED_POSITIONS]
        for team in teams
    }

    lineups: dict[tuple[str, int], tuple[str, ...]] = {}
    for week in weeks:
        available = {p.player_id for p in pool.players if not pool.on_bye(p.player_id, week)}

        for position in STREAMED_POSITIONS:
            current = {t: p for t, p in owners[position].items() if p is not None}
            _stream(pool, position, current, waiver_order, projector, skill, available)
            owners[position].update(current)

        for team in teams:
            roster = list(held[team])
            roster.extend(
                owners[position][team]
                for position in STREAMED_POSITIONS
                if owners[position][team] is not None
            )
            lineups[(team, week)] = _fill_lineup(
                settings, roster, positions, projector, team, available
            )

        # Only now does the week's production become known to next week's projections.
        for player_id in positions:
            projector.observe(player_id, week)

    return League(
        key=f"synthetic:{pool.season}:{seed}",
        settings=settings,
        lines=pool.lines,
        rosters=rosters,
        lineups=lineups,
        schedule=schedule,
        positions=positions,
        meta={"skill": skill, "seed": seed, "stack": hoarder},
    )
