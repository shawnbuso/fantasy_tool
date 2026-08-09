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
    root: Annotated[Path, typer.Option(help="Store location")] = store.DEFAULT_ROOT,
) -> None:
    """Measure whether positions are worth the same, and solve for increases that even them.

    Raw points per game of the startable players at each position, which is what a
    manager compares when filling a flex slot.
    """
    from .balance import FLEX_POSITIONS, profile, solve, startable_pool
    from .scoring import load_ruleset

    ruleset = load_ruleset(rules)
    wanted = _parse_seasons(seasons)
    lever_names = [name.strip() for name in levers.split(",") if name.strip()]
    premium_pair = None
    if premium and premium.lower() != "none":
        position, _, stat = premium.partition(".")
        premium_pair = (position.strip().upper(), stat.strip())

    stats = sorted({*lever_names, *([premium_pair[1]] if premium_pair else [])})
    pool = startable_pool(teams)

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
        means = {p: profiles[p].mean_points for p in FLEX_POSITIONS if p in profiles}
        table.add_row(
            f"{value:g}",
            *[f"{means[p]:.1f}" for p in FLEX_POSITIONS],
            f"{max(means.values()) - min(means.values()):.1f}",
        )
        solutions[value] = (variant, profiles, solve(profiles, lever_names, premium_pair))

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
            current = variant.scoring.get(lever, 0.0)
            per_point = 1 / (current + delta) if current + delta else 0
            console.print(
                f"    {lever}: {current:g} -> [bold]{current + delta:.4g}[/bold]"
                f"  (1 point per {per_point:.1f} yards)"
            )
        for position, (stat, rate) in solution.premiums.items():
            shared = variant.scoring.get(stat, 0.0) + solution.increments.get(stat, 0.0)
            console.print(
                f"    {position} premium on {stat}: [bold]+{rate:.4g}[/bold] "
                f"(so {position} scores {shared + rate:.4g} a unit vs {shared:.4g} for others)"
            )


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
