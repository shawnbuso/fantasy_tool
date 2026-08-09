# fantasy_tool

Test candidate fantasy football rules against historical NFL data, before inflicting
them on the league.

The problem this solves: a house rule that sounds fun often turns out to decide games.
"Defense gives up 40+ points → −40" and "kicker hits a 50+ yard FG → 50 points" are
swings larger than a typical margin of victory, so whoever triggers them wins
regardless of how well they drafted or managed. This tool measures that before the
season instead of discovering it in week 6.

## Status

Under construction. Working today: the persisted NFL data store, and a stat registry
at full parity with Yahoo's Scoring Settings page — all 74 categories, including the
ones disabled by default.

## Setup

```bash
uv sync
uv run fantasy-tool sync --seasons 2018-2024   # one time, ~12s, ~1.4MB
uv run fantasy-tool info
uv run fantasy-tool stats                      # the registry, as a settings page
```

`sync` downloads nflverse data, computes every derived field, and writes one parquet
file per season under `data/`. Simulations read only that store and never touch the
network. Re-running is a no-op; `--force` refreshes.

We persist *stats*, never *scores* — scores are the output of the rules under test,
so they get recomputed on every run.

Set `NFLREADPY_CACHE=filesystem NFLREADPY_CACHE_DIR=.cache` to cache the raw nflverse
downloads between syncs.

## Scoring conventions

These are judgment calls that materially change how often a rule fires. Documented
here because they're the first thing to check when a number looks wrong.

**Points allowed** counts *all* points the opposing NFL team scored, including their
defensive and special-teams touchdowns. This matches Yahoo, and it surprises people:
a pick-six thrown by the quarterback your D/ST is facing still counts against your
D/ST. Directly determines how often a "40+ points allowed" rule triggers.

**Yards allowed** is total net yards, the official convention: gross passing yards,
less sack yardage, plus rushing. Note that nflreadpy stores `sack_yards_lost` as a
*negative* number, so the natural-looking `passing − sacks + rushing` silently adds
sack yardage back and inflates every team by ~30 yards a game. Pinned to a real box
score in `tests/test_nfl_source.py`.

**Fumbles lost** uses `fumbles_lost_total`, not the sum of the passing, rushing, and
receiving fumble columns. Return-game and aborted-snap fumbles land only in the total.

**Where Yahoo is coarser than nflreadpy**, we aggregate down to match Yahoo: a single
`fg_made_50_` bucket (nflreadpy splits 50-59 and 60+), and a single
`two_pt_conversions` category (nflreadpy splits passing/rushing/receiving).

**Kick distances** come from `fg_made_list`, which is *semicolon*-delimited (`"29;31"`).
Exact yardages are preserved per kick, so a rule can bucket kicks any way it likes —
50+, 55+, per-yard — without touching play-by-play.

**Points allowed and yards allowed are scored as bands, not lookups.** Yahoo models
each band ("Points Allowed 28-34") as its own on/off category with its own value, so
we store them as 0/1 indicator stats. Scoring is then a plain sum of
`multiplier x stat` with no tier machinery, and a band a league leaves disabled — like
Points Allowed 21-27 in this league — is simply absent from the YAML.

**Thirteen categories need play-by-play**, not the weekly tables: long *touchdowns*
(as distinct from long plays), touchdowns split by how the ball was turned over, and
drive-level defensive stops. These are derived in `sources/pbp.py` at sync time.

**Laterals break the obvious derivation of long touchdowns.** Goff to St. Brown for 1
yard, lateral to Williams for 41 and the score, is a 42-yard *passing* touchdown but
not a 40-yard *receiving* touchdown for St. Brown. Ball-carrier categories therefore
require the player to have actually scored, not just to have been on the play.

## Layout

```
src/fantasy_tool/
├── cli.py          # typer commands
├── model.py        # StatLine: the source-agnostic contract
├── stats.py        # the registry: a text mirror of Yahoo's settings page
├── store.py        # sync / load persisted parquet
└── sources/
    ├── nfl.py      # weekly tables -> StatLine (sync time only)
    └── pbp.py      # play-by-play categories (sync time only)
```
