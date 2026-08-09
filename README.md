# fantasy_tool

Test candidate fantasy football rules against historical NFL data, before inflicting
them on the league.

The problem this solves: a house rule that sounds fun often turns out to decide games.
"Defense gives up 40+ points → −40" and "kicker hits a 50+ yard FG → 50 points" are
swings larger than a typical margin of victory, so whoever triggers them wins
regardless of how well they drafted or managed. This tool measures that before the
season instead of discovering it in week 6.

## Status

Under construction. Working today: the persisted NFL data store, a stat registry at
full parity with Yahoo's Scoring Settings page (all 74 categories, including the ones
disabled by default), and the YAML rule sets and scorer built on top.

The synthetic league generator and season simulator work too:

```bash
uv run fantasy-tool sim --rules rules/house_2026.yaml --season 2024 --seed 7
```

Still to come: the counterfactual analysis that turns two runs into a verdict.

## How the simulation is calibrated

A simulator that runs cleanly but produces a league behaving nothing like real fantasy
football would give confident, wrong answers. The headline metric is a rule's swing
measured against a typical margin of victory, so the margin has to be right.

| | target | why |
|---|---|---|
| median margin of victory | 20–25 | the denominator of every verdict |
| lineup efficiency | 85–90% | higher means hindsight, lower means noise |
| season points spread | 1.5–2× | best team vs worst |

All three are asserted in `tests/test_sim.py`.

Getting there needed four corrections worth remembering, each of which produced a
league that *looked* fine:

- **Draft on value over replacement, not raw points.** A quarterback outscores nearly
  everyone, but you start one and the waiver wire has another nearly as good. Ranking
  on raw value builds rosters with three quarterbacks and three receivers that then
  can't field a legal lineup.
- **Scale manager error to the data.** Draft value has a spread of about 3 points;
  noise was set at 14. The draft was effectively random, which produced rosters that
  couldn't score. Both noise terms are now multiples of the observed spread, so they
  stay calibrated when the scoring rules change.
- **The draft board must include breakouts.** Trimming it by prior-season value alone
  excludes everyone who breaks out this year — precisely the players who make lineups
  good. Membership now looks at both seasons; draft *order* still uses only the prior
  one, so breakouts go late and reward whoever took the flyer.
- **Managers know byes, not outcomes.** The stat feed only carries players who recorded
  something, so absence means "didn't produce", not "didn't play". Bye weeks are
  derived exactly (a team is on bye iff its defense has no row), and a blank non-bye
  week counts as a zero — which is how a manager notices a player has stopped
  producing and benches him.

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

## Rule sets

`rules/base_yahoo.yaml` is the league's current settings. A category is enabled by
giving it a value; anything omitted is switched off in Yahoo.

```bash
uv run fantasy-tool validate rules/base_yahoo.yaml
uv run fantasy-tool score --rules rules/base_yahoo.yaml --season 2024 --week 1 --position K
```

A candidate rule set starts from `extends: base_yahoo.yaml` and lists only what
changes; setting a category to `null` turns it off. The merge is one level deep on
purpose, so a candidate always reads as "the base, plus these specific changes".

Validation is deliberately strict, because a typo that silently scored zero would
corrupt an analysis while looking entirely plausible. Unknown categories are rejected
with a suggestion, bonuses are held to Yahoo's limits (three tiers, and only on
passing/rushing/receiving yards), and the 26-category offensive cap is enforced.

## Custom rules

Yahoo can only multiply a stat by a number and add three yardage bonuses. Anything
conditional — a penalty past a threshold, a boost that depends on the standings, a
streak — has to be Python. A rule takes one player-week in context and returns a point
delta; `rules/house_2026.py` holds the current candidates.

```python
@rule("fg_long_bonus", positions=["K"])
def fg_long_bonus(ctx: RuleContext) -> float:
    """Big bonus for a long field goal."""
    made = sum(
        1 for y in ctx.line.events.get("fg_made_yards", ()) if y >= ctx.param("min_yards", 50)
    )
    return ctx.param("bonus", 50.0) * made
```

Enable it from YAML, where every threshold is a parameter so magnitudes can be swept
later without editing code:

```yaml
custom_rules:
  modules: [house_2026.py]
  enabled:
    fg_long_bonus: {min_yards: 50, bonus: 50}
```

The context carries the player's line and base points, the manager's own starters,
this week's opposing starters, and every completed week. Two constraints are
deliberate:

- **Rules see teammates and opponents base-scored only**, never another rule's output,
  so rules are order-independent and there is no "which one ran first" class of bug.
- **`history` holds only completed weeks**, so a rule structurally cannot see the
  future — enforced by what's in the object, not by a convention asking nicely.

Prefer `.base` over `.total` when reading history for anything streak-shaped, or the
bonus ends up feeding the streak that earns it.

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

**A kicker is not confined to kicking categories.** Yahoo's Offense / Kickers / Defense
headings organise the settings page; they don't restrict who can earn what. Fake field
goals are in the data — Chris Boswell threw a touchdown, Jason Sanders caught one, Jake
Elliott threw an interception — and Yahoo pays out for all of them. The only real
boundary is between players and team D/ST units, which are different kinds of entity.

**nflverse's own `fantasy_points_ppr` uses a narrower fumble rule than Yahoo**, summing
the three positional fumble columns and so missing return fumbles. That difference is
the *only* permitted disagreement in the scoring oracle test, which checks every
offensive player-week across every synced season (~45,000 lines) and requires each
mismatch to be explained exactly by it.

## Layout

```
rules/
├── base_yahoo.yaml   # the league's real current settings
├── house_2026.yaml   # candidates: extends the base
└── house_2026.py     # the Python rule bodies

src/fantasy_tool/
├── cli.py            # typer commands
├── model.py          # StatLine, TeamWeek, History: the source-agnostic contract
├── stats.py          # the registry: a text mirror of Yahoo's settings page
├── scoring.py        # YAML rule sets and the base scorer
├── rules.py          # the @rule registry and RuleContext
├── store.py          # sync / load persisted parquet
└── sources/
    ├── nfl.py        # weekly tables -> StatLine (sync time only)
    └── pbp.py        # play-by-play categories (sync time only)
```
