"""The persisted local store.

`sync` resolves everything expensive once -- downloading nflverse data and computing
every derived field -- and writes one parquet file per season. Simulations read only
from here, so no simulation run ever touches the network.

We persist stats, never scores: scores are the output of the rules under test, so
they have to be recomputed on every run. The slow part is the download-and-derive
step, and that is exactly what becomes one-time.
"""

import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import polars as pl

from .model import StatLine
from .sources.nfl import EVENT_COLUMNS, STAT_COLUMNS, build_season

DEFAULT_ROOT = Path("data")


def season_path(root: Path, season: int) -> Path:
    return root / "statlines" / f"{season}.parquet"


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def read_manifest(root: Path) -> dict:
    path = _manifest_path(root)
    return json.loads(path.read_text()) if path.exists() else {"seasons": {}}


def sync(seasons: list[int], root: Path = DEFAULT_ROOT, force: bool = False) -> list[int]:
    """Download and persist the given seasons. Returns the seasons actually written."""
    (root / "statlines").mkdir(parents=True, exist_ok=True)
    manifest = read_manifest(root)
    written = []

    for season in seasons:
        path = season_path(root, season)
        if path.exists() and not force:
            continue
        frame = build_season(season)
        frame.write_parquet(path)
        manifest["seasons"][str(season)] = {
            "rows": frame.height,
            "synced": datetime.now(tz=UTC).date().isoformat(),
            "nflreadpy": version("nflreadpy"),
        }
        written.append(season)

    _manifest_path(root).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return written


def load_frame(season: int, root: Path = DEFAULT_ROOT) -> pl.DataFrame:
    path = season_path(root, season)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found -- run `fantasy-tool sync --seasons {season}`")
    return pl.read_parquet(path)


def load_statlines(season: int, root: Path = DEFAULT_ROOT) -> dict[tuple[str, int], StatLine]:
    """Every player-week of a season, keyed by (player_id, week)."""
    frame = load_frame(season, root)
    lines: dict[tuple[str, int], StatLine] = {}

    for row in frame.iter_rows(named=True):
        line = StatLine(
            player_id=row["player_id"],
            name=row["name"],
            position=row["position"],
            nfl_team=row["nfl_team"],
            season=row["season"],
            week=row["week"],
            opponent=row["opponent"] or "",
            stats={k: row[k] for k in STAT_COLUMNS if row[k]},
            events={k: tuple(row[k]) for k in EVENT_COLUMNS if row[k]},
        )
        lines[(line.player_id, line.week)] = line

    return lines
