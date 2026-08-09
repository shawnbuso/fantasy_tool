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
