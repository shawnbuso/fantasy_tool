"""Command line entry point."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import store

app = typer.Typer(help="Test candidate fantasy football rules against historical NFL data.")
console = Console()


def _parse_seasons(spec: str) -> list[int]:
    """Accept '2024', '2019-2024', or '2019,2021,2024'."""
    seasons: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            seasons.extend(range(lo, hi + 1))
        else:
            seasons.append(int(part))
    return sorted(set(seasons))


@app.command()
def sync(
    seasons: Annotated[str, typer.Option(help="e.g. 2024, 2019-2024, or 2019,2021")],
    root: Annotated[Path, typer.Option(help="Store location")] = store.DEFAULT_ROOT,
    force: Annotated[bool, typer.Option(help="Re-download seasons already present")] = False,
) -> None:
    """Download NFL stats and persist them locally. Simulations read only this store."""
    wanted = _parse_seasons(seasons)
    with console.status(f"Syncing {len(wanted)} season(s)..."):
        written = store.sync(wanted, root=root, force=force)

    skipped = [s for s in wanted if s not in written]
    if written:
        console.print(f"[green]Wrote[/green] {', '.join(str(s) for s in written)}")
    if skipped:
        console.print(
            f"[dim]Already present (use --force to refresh): "
            f"{', '.join(str(s) for s in skipped)}[/dim]"
        )


@app.command()
def stats(
    section: Annotated[str | None, typer.Option(help="Filter, e.g. Kickers")] = None,
    unsupported: Annotated[bool, typer.Option(help="Show only what we can't score yet")] = False,
) -> None:
    """List every scoring category, mirroring Yahoo's Scoring Settings page."""
    from .stats import SECTIONS, STATS

    rows = [s for s in STATS if not unsupported or not s.supported]
    if section:
        rows = [s for s in rows if s.section.lower().startswith(section.lower())]
    if not rows:
        console.print("[yellow]No categories matched.[/yellow]")
        raise typer.Exit(1)

    for name in SECTIONS:
        in_section = [s for s in rows if s.section == name]
        if not in_section:
            continue
        table = Table(title=name.upper(), title_justify="left", show_lines=False)
        table.add_column("YAML key", style="cyan")
        table.add_column("Yahoo label")
        table.add_column("Group", style="dim")
        table.add_column("Scoreable")
        for stat in in_section:
            mark = "[green]yes[/green]" if stat.supported else "[red]needs play-by-play[/red]"
            table.add_row(stat.key, stat.label, stat.group, mark)
        console.print(table)
        console.print()

    missing = [s for s in STATS if not s.supported]
    console.print(
        f"[dim]{len(STATS)} categories, {len(STATS) - len(missing)} scoreable "
        f"from the current store.[/dim]"
    )


@app.command()
def validate(
    rules: Annotated[Path, typer.Argument(help="Rule set YAML")],
) -> None:
    """Parse a rule set and print what it enables."""
    from pydantic import ValidationError

    from .scoring import load_ruleset
    from .stats import STAT_BY_KEY

    try:
        ruleset = load_ruleset(rules)
    except (ValidationError, ValueError) as exc:
        console.print(f"[red]{rules} is not valid:[/red]\n{exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]OK[/green]  {ruleset.name}")
    console.print(f"Starters: {' '.join(ruleset.lineup.starters)}  (+{ruleset.lineup.bench} bench)")
    console.print(
        f"Fractional points: {ruleset.options.fractional_points}   "
        f"Negative points: {ruleset.options.negative_points}\n"
    )

    table = Table("Category", "Yahoo label", "Value")
    for key, value in ruleset.scoring.items():
        table.add_row(key, STAT_BY_KEY[key].label, f"{value:g}")
    for key, yards in ruleset.yards_per_point.items():
        table.add_row(key, STAT_BY_KEY[key].label, f"{yards:g} yards = 1 point")
    console.print(table)

    for key, bonuses in ruleset.bonuses.items():
        tiers = ", ".join(f"{b.target:g}+ -> +{b.points:g}" for b in bonuses)
        console.print(f"Bonus on {key}: {tiers}")

    if not ruleset.custom_rules.enabled:
        return

    from .rules import registered

    known = registered()
    console.print()
    custom = Table("Custom rule", "Applies to", "Parameters", "What it does")
    for name, params in ruleset.custom_rules.enabled.items():
        entry = known[name]
        applies = "any" if len(entry.positions) > 5 else " ".join(sorted(entry.positions))
        shown = ", ".join(f"{k}={v}" for k, v in params.items()) or "defaults"
        custom.add_row(name, applies, shown, entry.doc)
    console.print(custom)


@app.command()
def score(
    rules: Annotated[Path, typer.Option(help="Rule set YAML")],
    season: Annotated[int, typer.Option(help="Season to score")],
    week: Annotated[int, typer.Option(help="Week to score")],
    top: Annotated[int, typer.Option(help="How many to show")] = 20,
    position: Annotated[str | None, typer.Option(help="Filter, e.g. K or DEF")] = None,
    root: Annotated[Path, typer.Option(help="Store location")] = store.DEFAULT_ROOT,
) -> None:
    """Score one week under a rule set. A quick eyeball check that rules do what you meant."""
    from .scoring import load_ruleset, score_base

    ruleset = load_ruleset(rules)
    lines = store.load_statlines(season, root=root)
    scored = [
        (score_base(line, ruleset), line)
        for line in lines.values()
        if line.week == week and (position is None or line.position == position.upper())
    ]
    if not scored:
        console.print(f"[yellow]No lines for {season} week {week}.[/yellow]")
        raise typer.Exit(1)

    scored.sort(key=lambda pair: pair[0], reverse=True)
    table = Table(title=f"{ruleset.name} -- {season} week {week}", title_justify="left")
    table.add_column("Points", justify="right")
    table.add_column("Player")
    table.add_column("Pos")
    table.add_column("Team")
    for points, line in scored[:top]:
        table.add_row(f"{points:.2f}", line.name, line.position, line.nfl_team)
    console.print(table)


@app.command()
def sim(
    rules: Annotated[Path, typer.Option(help="Rule set YAML")],
    season: Annotated[int, typer.Option(help="Season to replay")],
    seed: Annotated[int, typer.Option(help="Same seed reproduces the same league")] = 7,
    teams: Annotated[int, typer.Option(help="League size")] = 10,
    root: Annotated[Path, typer.Option(help="Store location")] = store.DEFAULT_ROOT,
) -> None:
    """Replay one synthetic league-season and print the final table."""
    from .scoring import load_ruleset
    from .sim import simulate, standings_table
    from .sources.synthetic import build_pool, generate

    ruleset = load_ruleset(rules)
    with console.status(f"Building {season} player pool..."):
        pool = build_pool(season, ruleset, root=root, teams=teams)
    league = generate(pool, seed, ruleset, n_teams=teams)
    result = simulate(league, ruleset)

    skill = league.meta["skill"]
    table = Table(title=f"{ruleset.name} -- {league.key}", title_justify="left")
    table.add_column("Team")
    table.add_column("Skill", justify="right")
    table.add_column("W-L-T")
    table.add_column("Points for", justify="right")
    table.add_column("Points against", justify="right")
    for team, record in standings_table(result):
        table.add_row(
            team,
            f"{skill[team]:.2f}",
            f"{record.wins}-{record.losses}-{record.ties}",
            f"{record.points_for:.1f}",
            f"{record.points_against:.1f}",
        )
    console.print(table)

    fired: dict[str, float] = {}
    for week in result.weeks:
        for team_week in week.team_weeks.values():
            for line in team_week.scored:
                for name, delta in line.rule_points.items():
                    fired[name] = fired.get(name, 0.0) + delta
    if fired:
        console.print()
        console.print("[bold]Custom rules[/bold]")
        for name, total in sorted(fired.items()):
            console.print(f"  {name}: {total:+.1f} points across the season")


@app.command()
def evaluate(
    base: Annotated[Path, typer.Option(help="Baseline rule set")],
    candidate: Annotated[Path, typer.Option(help="Candidate rule set to test")],
    seasons: Annotated[str, typer.Option(help="e.g. 2019-2024")] = "2019-2024",
    leagues: Annotated[int, typer.Option(help="Synthetic leagues per season")] = 20,
    seed: Annotated[int, typer.Option(help="Base seed; leagues vary from it")] = 7,
    teams: Annotated[int, typer.Option(help="League size")] = 10,
    csv: Annotated[Path | None, typer.Option(help="Write per-matchup rows here")] = None,
    root: Annotated[Path, typer.Option(help="Store location")] = store.DEFAULT_ROOT,
) -> None:
    """Measure what a candidate rule set would have done, against the baseline.

    Each league is generated once from the baseline and simulated twice, so the two
    runs share a draft, a schedule, and lineups and the only difference is the rules.
    """
    from .analysis import compare
    from .harness import build_leagues, run_pairs, skills
    from .report import render
    from .scoring import load_ruleset

    baseline_rules = load_ruleset(base)
    candidate_rules = load_ruleset(candidate)

    with console.status("Simulating...") as status:
        built = build_leagues(
            _parse_seasons(seasons),
            baseline_rules,
            leagues=leagues,
            seed=seed,
            teams=teams,
            root=root,
            progress=status.update,
        )
        pairs = run_pairs(built, baseline_rules, candidate_rules)

    analysis = compare(pairs, baseline_rules, candidate_rules, skills(built))
    render(analysis, console, baseline=baseline_rules.name, candidate=candidate_rules.name)

    if csv:
        import csv as csv_module

        from .analysis import diff_season

        rows = [d for pair in pairs for d in diff_season(*pair)]
        with csv.open("w", newline="") as handle:
            writer = csv_module.writer(handle)
            writer.writerow(
                [
                    "league",
                    "week",
                    "home",
                    "away",
                    "home_base",
                    "away_base",
                    "home_candidate",
                    "away_candidate",
                    "swing",
                    "triggered",
                    "flipped",
                ]
            )
            for row in rows:
                writer.writerow(
                    [
                        row.league_key,
                        row.week,
                        row.home,
                        row.away,
                        f"{row.home_base:.2f}",
                        f"{row.away_base:.2f}",
                        f"{row.home_candidate:.2f}",
                        f"{row.away_candidate:.2f}",
                        f"{row.swing:.2f}",
                        int(row.triggered),
                        int(row.flipped),
                    ]
                )
        console.print(f"\n[dim]Wrote {len(rows):,} matchup rows to {csv}[/dim]")


@app.command()
def sweep(
    base: Annotated[Path, typer.Option(help="Baseline rule set")],
    candidate: Annotated[Path, typer.Option(help="Candidate rule set to tune")],
    param: Annotated[
        list[str],
        typer.Option(help="rule.param=v1,v2,v3 -- repeat the flag to sweep a grid"),
    ],
    seasons: Annotated[str, typer.Option(help="e.g. 2019-2024")] = "2019-2024",
    leagues: Annotated[int, typer.Option(help="Synthetic leagues per season")] = 20,
    seed: Annotated[int, typer.Option(help="Base seed; leagues vary from it")] = 7,
    teams: Annotated[int, typer.Option(help="League size")] = 10,
    root: Annotated[Path, typer.Option(help="Store location")] = store.DEFAULT_ROOT,
) -> None:
    """Try a range of values for a rule's parameters and report how each one lands.

    Knowing a rule is too swingy is half an answer; this gives the other half. Every
    setting is measured against the same leagues, so the rows are directly comparable.
    """
    from . import sweep as sweep_module
    from .harness import build_leagues, skills
    from .report import render_sweep
    from .scoring import load_ruleset

    baseline_rules = load_ruleset(base)
    candidate_rules = load_ruleset(candidate)
    if not candidate_rules.custom_rules.enabled:
        console.print(f"[red]{candidate} enables no custom rules, so there's nothing to sweep.")
        raise typer.Exit(1)

    try:
        for spec in param:
            sweep_module.parse_spec(spec)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    with console.status("Building leagues...") as status:
        built = build_leagues(
            _parse_seasons(seasons),
            baseline_rules,
            leagues=leagues,
            seed=seed,
            teams=teams,
            root=root,
            progress=status.update,
        )
        try:
            points = sweep_module.run(
                built,
                baseline_rules,
                candidate_rules,
                param,
                skills=skills(built),
                progress=status.update,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

    render_sweep(
        points,
        console,
        candidate=candidate_rules.name,
        monotonic=sweep_module.is_monotonic(points),
        best=sweep_module.recommend(points),
    )


@app.command()
def balance(
    rules: Annotated[Path, typer.Option(help="Rule set whose lineup and scoring to measure")],
    seasons: Annotated[str, typer.Option(help="e.g. 2019-2024")] = "2019-2024",
    ppr: Annotated[str, typer.Option(help="Reception values to compare")] = "0,0.5,1.0",
    teams: Annotated[int, typer.Option(help="League size")] = 10,
    levers: Annotated[
        str, typer.Option(help="Yahoo categories to raise")
    ] = "receiving_yards,rushing_yards",
    premium: Annotated[
        str, typer.Option(help="POSITION.stat scored for that position only")
    ] = "TE.receiving_yards",
    positions: Annotated[
        str, typer.Option(help="Positions to level; default is everything the flex admits")
    ] = "",
    root: Annotated[Path, typer.Option(help="Store location")] = store.DEFAULT_ROOT,
) -> None:
    """Measure whether positions are worth the same, and solve for increases that even them.

    Raw points per game of the startable players at each position, which is what a
    manager compares when filling a flex slot.
    """
    from .balance import FLEX_POSITIONS, profile, solve, startable_pool
    from .model import parse_slots
    from .scoring import load_ruleset
    from .sources.synthetic import lineup_shape

    ruleset = load_ruleset(rules)
    wanted = _parse_seasons(seasons)
    lever_names = [name.strip() for name in levers.split(",") if name.strip()]
    premium_pair = None
    if premium and premium.lower() != "none":
        position, _, stat = premium.partition(".")
        premium_pair = (position.strip().upper(), stat.strip())

    stats = sorted({*lever_names, *([premium_pair[1]] if premium_pair else [])})

    # Only positions competing for a flex slot need balancing. One with nothing but a
    # dedicated slot is started once by every team, so scoring less costs nobody.
    _, flex = lineup_shape(parse_slots(ruleset.lineup.starters))
    eligible = tuple(p for p in FLEX_POSITIONS if any(p in slot.eligible for slot in flex))
    if not eligible:
        console.print("[yellow]No flex slots in this lineup, so no position competes.[/yellow]")
        raise typer.Exit(1)

    # Eligible for the flex is not the same as competing for it. A position far enough
    # behind that it never wins a slot is in the same position as one with no flex
    # eligibility at all -- every team starts its one and the shortfall is symmetric --
    # so `--positions` narrows the solve to the ones actually in contention.
    competing = eligible
    if positions:
        competing = tuple(p.strip().upper() for p in positions.split(",") if p.strip())
        unknown = [p for p in competing if p not in FLEX_POSITIONS]
        if unknown:
            console.print(f"[red]Not flex positions: {', '.join(unknown)}[/red]")
            raise typer.Exit(1)

    pool = startable_pool(teams, flex_share=len(flex) / len(competing))
    note = ""
    if set(competing) != set(eligible):
        left_out = ", ".join(p for p in eligible if p not in competing)
        note = f" The flex also admits {left_out}, left out of the solve on purpose."
    console.print(
        f"[dim]Balancing {', '.join(competing)} -- the positions sharing "
        f"{len(flex)} flex slot(s). Others are started once by everyone.{note}[/dim]"
    )

    table = Table(title=f"Points per game, top {pool} at each position", title_justify="left")
    table.add_column("Receptions")
    for position in FLEX_POSITIONS:
        table.add_column(position, justify="right")
    table.add_column("Spread", justify="right")

    solutions = {}
    for value in (float(v) for v in ppr.split(",")):
        variant = ruleset.model_copy(update={"scoring": {**ruleset.scoring, "receptions": value}})
        with console.status(f"Profiling at {value:g} PPR..."):
            profiles = profile(wanted, variant, stats, top_n=pool, root=root)
        means = {p: profiles[p].mean_points for p in competing}
        table.add_row(
            f"{value:g}",
            *[f"{profiles[p].mean_points:.1f}" if p in profiles else "-" for p in FLEX_POSITIONS],
            f"{max(means.values()) - min(means.values()):.1f}",
        )
        solutions[value] = (
            variant,
            profiles,
            solve(profiles, lever_names, premium_pair, competing),
        )

    console.print(table)

    for value, (variant, profiles, solution) in solutions.items():
        console.print()
        console.print(f"[bold]At {value:g} points per reception[/bold]")
        if solution is None:
            console.print(
                "  [red]No solution.[/red] There must be exactly one lever per position "
                "being lifted; add or remove one."
            )
            continue
        if not solution.feasible:
            console.print(
                "  [yellow]Needs a reduction somewhere, so it can't be done with "
                "increases alone.[/yellow] Negative values below show what it would take."
            )
        console.print(f"  Everyone lands at [bold]{solution.target:.1f}[/bold] points per game.")
        for lever, delta in solution.increments.items():
            current = variant.points.get(lever, 0.0)
            was = 1 / current if current else 0
            now = 1 / (current + delta) if current + delta else 0
            console.print(f"    {lever}: [bold]{now:.4g} yards = 1 point[/bold]  (was {was:.4g})")
        for position, (stat, rate) in solution.premiums.items():
            shared = variant.points.get(stat, 0.0) + solution.increments.get(stat, 0.0)
            console.print(
                f"    {position} premium on {stat}: [bold]+{rate:.4g}[/bold] "
                f"(so {position} scores {shared + rate:.4g} a unit vs {shared:.4g} for others)"
            )


@app.command()
def flatten(
    rules: Annotated[Path, typer.Argument(help="Rule set to resolve")],
    out: Annotated[Path, typer.Option(help="Where to write the flat file")],
    name: Annotated[str | None, typer.Option(help="Name for the resolved league")] = None,
) -> None:
    """Resolve an extends chain into one standalone file.

    Layered configs are good for measuring one change at a time and useless to read
    off while filling in a settings page. This writes what they add up to, so the
    reference copy is generated rather than transcribed.
    """
    from .scoring import load_ruleset
    from .stats import STAT_BY_KEY

    ruleset = load_ruleset(rules)
    if name:
        ruleset = ruleset.model_copy(update={"name": name})

    grouped: dict[str, list[tuple[str, float]]] = {}
    for key, value in ruleset.scoring.items():
        stat = STAT_BY_KEY[key]
        grouped.setdefault(f"{stat.section} / {stat.group}", []).append((key, value))

    body = []
    for group, items in grouped.items():
        body.append(f"\n  # --- {group}")
        body.extend(f"  {key}: {value:g}" for key, value in items)

    tiers = []
    for category, bonuses in ruleset.bonuses.items():
        tiers.append(f"  {category}:")
        tiers.extend(f"    - {{target: {b.target:g}, points: {b.points:g}}}" for b in bonuses)

    custom = ""
    if ruleset.custom_rules.enabled:
        custom = (
            "\ncustom_rules:\n  modules: ["
            + ", ".join(str(m) for m in ruleset.custom_rules.modules)
            + "]\n  enabled:\n"
        )
        for rule_name, params in ruleset.custom_rules.enabled.items():
            custom += f"    {rule_name}:\n"
            custom += "".join(f"      {k}: {v}\n" for k, v in params.items())

    text = f"""# {ruleset.name}
#
# Generated by `fantasy-tool flatten` from {rules.name}; edit the layered configs and
# regenerate rather than editing this. Yardage is written the way Yahoo's settings
# page asks for it: how many yards make one point.

name: "{ruleset.name}"

lineup:
  starters: [{", ".join(ruleset.lineup.starters)}]
  bench: {ruleset.lineup.bench}

options:
  fractional_points: {str(ruleset.options.fractional_points).lower()}
  negative_points: {str(ruleset.options.negative_points).lower()}

yards_per_point:
{chr(10).join(f"  {k}: {v:g}" for k, v in ruleset.yards_per_point.items())}

scoring:{chr(10).join(body)}
"""
    if tiers:
        text += "\n# Cumulative: hitting the higher target pays the lower tier too.\nbonuses:\n"
        text += chr(10).join(tiers) + "\n"
    text += custom

    out.write_text(text)
    console.print(f"[green]Wrote[/green] {out}")


@app.command("yahoo-auth")
def yahoo_auth(
    state: Annotated[Path, typer.Option(help="Where to save the session")] = Path(
        "data/yahoo/session.json"
    ),
) -> None:
    """Log in to Yahoo once, in a real browser, and save the session.

    Yahoo's login is defended by two-factor prompts and device checks, so this step is
    manual by design. Everything after it is plain HTTP. Expect the session to last
    days to weeks, not months.
    """
    from .sources.yahoo.auth import capture

    try:
        written = capture(state)
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Saved session to[/green] {written}")


@app.command("yahoo-probe")
def yahoo_probe(
    league_id: Annotated[str, typer.Argument(help="Yahoo league id, e.g. 786986")],
    season: Annotated[int | None, typer.Option(help="Past season; omit for current")] = None,
    team: Annotated[int, typer.Option(help="Team id to sample")] = 1,
    week: Annotated[int, typer.Option(help="Week to sample")] = 1,
    state: Annotated[Path, typer.Option()] = Path("data/yahoo/session.json"),
    cache: Annotated[Path, typer.Option()] = Path("data/yahoo/pages"),
) -> None:
    """Fetch a single page and report what's in it.

    Run this before anything else. Nobody has published working selectors for Yahoo
    roster pages, so the parser has to be written against real markup -- this is how
    we get some.
    """
    from .sources.yahoo.fetch import Scraper, SessionExpired, matchup_url

    url = matchup_url(league_id, team, week, season)
    console.print(f"[dim]{url}[/dim]")
    try:
        with Scraper(state, cache) as scraper:
            page = scraper.get(url, key=f"probe/{season or 'current'}-w{week:02d}-t{team:02d}")
    except (SessionExpired, FileNotFoundError, ImportError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(
        f"[green]{'Read from cache' if page.from_cache else 'Fetched'}[/green] "
        f"{len(page.html):,} bytes -> {page.path}"
    )

    lowered = page.html.lower()
    table = Table("Signal", "Found", title="What's in the page", title_justify="left")
    table.add_row("<table> elements", str(lowered.count("<table")))
    table.add_row("looks like a login page", "yes" if "sign in" in lowered else "no")
    for marker in ("statTable", "Starters", "Bench", "fantasy points", "Proj"):
        table.add_row(f"contains {marker!r}", "yes" if marker.lower() in lowered else "no")
    console.print(table)
    console.print("\n[dim]Send me this file and I'll write the parser against it.[/dim]")


@app.command("yahoo-season")
def yahoo_season(
    league_id: Annotated[str, typer.Argument(help="That season's Yahoo league id")],
    season: Annotated[int, typer.Option(help="Season year")],
    weeks: Annotated[str, typer.Option(help="Fantasy regular season")] = "1-14",
    teams: Annotated[int, typer.Option()] = 10,
    state: Annotated[Path, typer.Option()] = Path("data/yahoo/session.json"),
    cache: Annotated[Path, typer.Option()] = Path("data/yahoo/pages"),
    root: Annotated[Path, typer.Option()] = store.DEFAULT_ROOT,
) -> None:
    """Download a real league-season and summarise what came back.

    Lineups come from Yahoo; every point is recomputed from NFL stats, so the scoring
    that season happened to use doesn't matter.
    """
    from .sources.yahoo.fetch import Scraper, SessionExpired
    from .sources.yahoo.season import build_league, fetch_season

    low, high = (int(x) for x in weeks.split("-"))
    span = range(low, high + 1)

    try:
        with console.status("Fetching...") as status, Scraper(state, cache) as scraper:
            by_week, schedule, names = fetch_season(
                scraper, league_id, season, span, teams, progress=status.update
            )
    except (SessionExpired, FileNotFoundError, ImportError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    league, unmatched = build_league(league_id, season, by_week, schedule, names, root=root)

    console.print(
        f"[green]{season}[/green]  {len(names)} teams, {len(by_week)} weeks, "
        f"{len(schedule)} matchups"
    )
    console.print(
        f"Lineup: {' '.join(s.label for s in league.settings.slots)} "
        f"(+{league.settings.bench} bench)"
    )
    if unmatched:
        table = Table("Unmatched player", "Weeks", title="Not found in the NFL id crosswalk")
        for name, count in sorted(unmatched.items(), key=lambda kv: -kv[1])[:15]:
            table.add_row(name, str(count))
        console.print(table)
        console.print("[yellow]These score nothing; usually retired or practice-squad.[/yellow]")
    else:
        console.print("[green]Every player matched the NFL id crosswalk.[/green]")


@app.command("yahoo-evaluate")
def yahoo_evaluate(
    league: Annotated[
        list[str], typer.Option(help="YEAR:LEAGUE_ID, repeatable -- e.g. 2024:583648")
    ],
    base: Annotated[Path, typer.Option(help="Baseline rule set")],
    candidate: Annotated[Path, typer.Option(help="Candidate rule set")],
    weeks: Annotated[str, typer.Option()] = "1-14",
    teams: Annotated[int, typer.Option()] = 10,
    state: Annotated[Path, typer.Option()] = Path("data/yahoo/session.json"),
    cache: Annotated[Path, typer.Option()] = Path("data/yahoo/pages"),
    root: Annotated[Path, typer.Option()] = store.DEFAULT_ROOT,
) -> None:
    """Replay the league's own seasons under two rule sets and compare.

    The lineups are the ones actually set, so this answers what a rule would have done
    to *this* league rather than to a simulated one. Pool as many seasons as you have:
    a single season is 70 matchups, which is far too few to pin a rate down.
    """
    from .analysis import compare
    from .report import render
    from .scoring import load_ruleset
    from .sim import simulate
    from .sources.yahoo.fetch import Scraper, SessionExpired
    from .sources.yahoo.season import build_league, fetch_season

    baseline_rules = load_ruleset(base)
    candidate_rules = load_ruleset(candidate)
    low, high = (int(x) for x in weeks.split("-"))

    wanted = []
    for entry in league:
        year, _, identifier = entry.partition(":")
        if not identifier:
            console.print(f"[red]Expected YEAR:LEAGUE_ID, got {entry!r}[/red]")
            raise typer.Exit(1)
        wanted.append((int(year), identifier))

    pairs, missing = [], {}
    try:
        with console.status("Fetching...") as status, Scraper(state, cache) as scraper:
            for year, identifier in sorted(wanted):
                parts = fetch_season(
                    scraper,
                    identifier,
                    year,
                    range(low, high + 1),
                    teams,
                    progress=status.update,
                )
                built, unmatched = build_league(identifier, year, *parts, root=root)
                missing.update(unmatched)
                pairs.append((simulate(built, baseline_rules), simulate(built, candidate_rules)))
    except (SessionExpired, FileNotFoundError, ImportError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if missing:
        console.print(f"[yellow]{len(missing)} players unmatched; they score nothing.[/yellow]")
    console.print(
        f"[dim]{len(pairs)} real season(s): {', '.join(str(y) for y, _ in sorted(wanted))}[/dim]"
    )

    analysis = compare(pairs, baseline_rules, candidate_rules, fixed_lineups=True)
    render(analysis, console, baseline=baseline_rules.name, candidate=candidate_rules.name)


@app.command()
def info(
    root: Annotated[Path, typer.Option(help="Store location")] = store.DEFAULT_ROOT,
) -> None:
    """Show what's in the local store."""
    manifest = store.read_manifest(root)
    if not manifest["seasons"]:
        console.print("[yellow]Store is empty.[/yellow] Run `fantasy-tool sync --seasons 2024`.")
        raise typer.Exit(1)

    table = Table("Season", "Rows", "Synced", "nflreadpy")
    for season, meta in sorted(manifest["seasons"].items()):
        table.add_row(season, f"{meta['rows']:,}", meta["synced"], meta["nflreadpy"])
    console.print(table)


if __name__ == "__main__":
    app()
