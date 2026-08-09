"""Rendering an Analysis for a human deciding whether to adopt a rule."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .analysis import Analysis, Rate, RuleImpact

VERDICT_STYLE = {
    "AUTO-DECIDE": "bold white on red",
    "LOTTERY TICKET": "bold white on red",
    "HIGH SWING": "bold black on yellow",
    "SPICY": "bold cyan",
    "FLAVOR": "dim",
}


def _pct(rate: Rate, *, with_interval: bool = False) -> str:
    if not with_interval or rate.total == 0:
        return f"{rate.rate:6.1%}"
    low, high = rate.interval
    return f"{rate.rate:6.1%} ({low:.1%}-{high:.1%})"


def _swing_ratio(impact: RuleImpact, median_margin: float) -> str:
    if not median_margin or not impact.median_swing:
        return "--"
    return f"{impact.median_swing / median_margin:.1f}x"


def render(analysis: Analysis, console: Console, *, baseline: str, candidate: str) -> None:
    console.print()
    console.print(f"[bold]{candidate}[/bold]  vs  [dim]{baseline}[/dim]")
    console.print(
        f"[dim]{analysis.league_seasons} league-seasons - {analysis.matchups:,} matchups[/dim]"
    )

    if not analysis.comparable_lineups:
        console.print(
            Panel(
                "The two rule sets use different lineup slots, so the lineups had to be\n"
                "re-derived and differ by construction. Part of the measured change is\n"
                "roster shape rather than scoring. Treat the numbers as directional.",
                title="not a clean counterfactual",
                border_style="yellow",
            )
        )

    _verdicts(analysis, console)
    _swing(analysis, console)
    _balance(analysis, console)
    _luck(analysis, console)
    _winners(analysis, console)


def render_sweep(points, console: Console, *, candidate: str, monotonic: bool, best) -> None:
    """One row per parameter setting, so a magnitude can be picked from evidence."""
    if not points:
        return
    first = points[0].analysis
    console.print()
    console.print(f"[bold]{candidate}[/bold] - parameter sweep")
    console.print(
        f"[dim]{first.league_seasons} league-seasons - {first.matchups:,} matchups "
        f"per setting - baseline margin {first.median_margin:.1f} points[/dim]"
    )
    console.print()

    table = Table(box=None, pad_edge=False)
    table.add_column("Setting")
    table.add_column("Fires", justify="right")
    table.add_column("Avg pts", justify="right")
    table.add_column("vs margin", justify="right")
    table.add_column("Decides", justify="right")
    table.add_column("Flips games", justify="right")
    table.add_column("Verdict")

    for point in points:
        impact = point.impact
        style = VERDICT_STYLE.get(impact.verdict, "")
        marker = " [bold green]<-[/bold green]" if best is not None and point is best else ""
        table.add_row(
            point.label + marker,
            _pct(impact.fired),
            f"{impact.mean_when_fired:+.1f}",
            _swing_ratio(impact, point.analysis.median_margin),
            _pct(impact.decisive),
            _pct(impact.flips, with_interval=True),
            f"[{style}] {impact.verdict} [/{style}]" if style else impact.verdict,
        )
    console.print(table)

    console.print()
    if best is None:
        console.print(
            "[bold red]Every setting tested decides games.[/bold red] This rule needs "
            "rethinking rather than tuning -- try a smaller effect, a cap, or a "
            "trigger that fires less often."
        )
    else:
        console.print(
            f"[bold green]Strongest setting that doesn't decide games: "
            f"{best.label}[/bold green] ({best.impact.verdict})."
        )

    if not monotonic:
        console.print(
            "[yellow]The flip rate isn't moving consistently with the setting, which "
            "means noise is swamping the signal. Re-run with more --leagues before "
            "reading a ranking off this.[/yellow]"
        )


def _verdicts(analysis: Analysis, console: Console) -> None:
    flagged = [r for r in analysis.per_rule if r.verdict in ("AUTO-DECIDE", "LOTTERY TICKET")]
    if not flagged:
        return
    console.print()
    for impact in flagged:
        console.print(
            Panel(
                f"[bold]{impact.name}[/bold] - {impact.note}.\n"
                f"When it fires it exceeds the game's margin "
                f"{impact.decisive.rate:.0%} of the time, and it fires in "
                f"{impact.fired.rate:.1%} of team-weeks.",
                title=impact.verdict,
                border_style="red",
            )
        )


def _swing(analysis: Analysis, console: Console) -> None:
    console.print()
    console.print("[bold]OUTCOME SWING[/bold]")

    table = Table(box=None, pad_edge=False)
    table.add_column("Rule")
    table.add_column("Fires", justify="right")
    table.add_column("Avg pts", justify="right")
    table.add_column("vs margin", justify="right")
    table.add_column("Flips games", justify="right")
    table.add_column("Verdict")

    for impact in (*analysis.per_rule, analysis.overall):
        style = VERDICT_STYLE.get(impact.verdict, "")
        table.add_row(
            impact.name,
            _pct(impact.fired),
            f"{impact.mean_when_fired:+.1f}",
            _swing_ratio(impact, analysis.median_margin),
            _pct(impact.flips, with_interval=True),
            f"[{style}] {impact.verdict} [/{style}]" if style else impact.verdict,
        )
    console.print(table)
    console.print(
        f"[dim]Typical margin of victory under the baseline: "
        f"{analysis.median_margin:.1f} points. 'vs margin' is the rule's median swing "
        f"as a multiple of it.[/dim]"
    )


def _delta_row(table: Table, label: str, base: float, candidate: float, note: str = "") -> None:
    table.add_row(label, f"{base:.3f}", f"{candidate:.3f}", f"{candidate - base:+.3f}", note)


def _balance(analysis: Analysis, console: Console) -> None:
    balance = analysis.balance
    console.print()
    console.print("[bold]COMPETITIVE BALANCE[/bold]")
    table = Table(box=None, pad_edge=False)
    for column in ("", "base", "candidate", "change", ""):
        table.add_column(column, justify="right" if column else "left")

    _delta_row(
        table,
        "Spread of wins (std dev)",
        balance.wins_stdev_base,
        balance.wins_stdev_candidate,
        "lower is more even",
    )
    _delta_row(
        table,
        "Underdog win rate",
        balance.underdog_rate_base.rate,
        balance.underdog_rate_candidate.rate,
        "higher is more even",
    )
    if balance.skill_correlation_base is not None:
        _delta_row(
            table,
            "Manager skill to win rate",
            balance.skill_correlation_base,
            balance.skill_correlation_candidate,
            "lower means skill matters less",
        )
    console.print(table)


def _luck(analysis: Analysis, console: Console) -> None:
    luck = analysis.luck
    console.print()
    console.print("[bold]LUCK VERSUS SKILL[/bold]")
    table = Table(box=None, pad_edge=False)
    for column in ("", "base", "candidate", "change", ""):
        table.add_column(column, justify="right" if column else "left")
    _delta_row(
        table,
        "Points scored to wins",
        luck.points_to_wins_base,
        luck.points_to_wins_candidate,
        "lower means scoring matters less",
    )
    _delta_row(
        table,
        "All-play to actual record",
        luck.allplay_to_actual_base,
        luck.allplay_to_actual_candidate,
        "lower means more schedule luck",
    )
    console.print(table)


def _winners(analysis: Analysis, console: Console) -> None:
    if not analysis.by_position:
        return
    console.print()
    console.print("[bold]WHERE THE POINTS LAND[/bold]")

    total = sum(abs(v) for v in analysis.by_position.values()) or 1.0
    table = Table(box=None, pad_edge=False)
    table.add_column("Position")
    table.add_column("Rule points", justify="right")
    table.add_column("Share", justify="right")
    for position, points in sorted(
        analysis.by_position.items(), key=lambda kv: abs(kv[1]), reverse=True
    ):
        table.add_row(position, f"{points:+,.0f}", f"{abs(points) / total:5.1%}")
    console.print(table)

    if analysis.by_team_skill:
        sharks = [d for s, d in analysis.by_team_skill if s >= 0.7]
        casuals = [d for s, d in analysis.by_team_skill if s <= 0.4]
        if sharks and casuals:
            console.print()
            console.print(
                f"[dim]Average change in wins: "
                f"stronger managers {sum(sharks) / len(sharks):+.2f}, "
                f"weaker managers {sum(casuals) / len(casuals):+.2f}.[/dim]"
            )
