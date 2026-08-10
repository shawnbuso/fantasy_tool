# fantasy_tool

Test candidate fantasy football rules against historical NFL data, before inflicting
them on the league.

The problem this solves: a house rule that sounds fun often turns out to decide games.
"Defense gives up 40+ points → −40" and "kicker hits a 50+ yard FG → 50 points" are
swings larger than a typical margin of victory, so whoever triggers them wins
regardless of how well they drafted or managed. This measures that before the season
instead of discovering it in week 6.

Built for one 10-team family league, but nothing is specific to it beyond
`rules/base_yahoo.yaml`.

## Setup

```bash
uv sync
uv run fantasy-tool sync --seasons 2018-2025   # one time, ~15s, ~1.6MB
uv run fantasy-tool stats                      # the registry, as a settings page
```

`sync` downloads nflverse data, computes every derived field, and writes one parquet
file per season under `data/`. Simulations read only that store and never touch the
network. Re-running is a no-op; `--force` refreshes.

We persist *stats*, never *scores* — scores are the output of the rules under test, so
they get recomputed on every run.

Set `NFLREADPY_CACHE=filesystem NFLREADPY_CACHE_DIR=.cache` to cache the raw nflverse
downloads between syncs.

## The workflow

```bash
# 1. Describe a candidate rule set (extends the baseline, lists only what changes)
uv run fantasy-tool validate rules/house_2026.yaml

# 2. Measure what it would have done, over thousands of synthetic league-seasons
uv run fantasy-tool evaluate --base rules/base_yahoo.yaml \
    --candidate rules/house_2026.yaml --seasons 2019-2024 --leagues 20

# 3. If it's too swingy, find a magnitude that isn't
uv run fantasy-tool sweep --base rules/base_yahoo.yaml --candidate rules/house_2026.yaml \
    --param "def_blowout_penalty.penalty=-4,-8,-12,-20,-40"

# 4. Sanity-check it against the league's own history
uv run fantasy-tool yahoo-evaluate --league 2024:583648 \
    --base rules/base_yahoo.yaml --candidate rules/league_2026.yaml
```

### All commands

| | |
|---|---|
| `sync` | download NFL stats into the local store |
| `stats` | list every scoring category, as a settings page |
| `info` | what's in the store |
| `validate` | parse a rule set and print what it enables |
| `score` | score one week — an eyeball check that rules do what you meant |
| `sim` | replay one synthetic league-season, print the table |
| `evaluate` | measure a candidate against a baseline |
| `sweep` | try a range of values for a parameter |
| `balance` | measure and solve position values |
| `flatten` | resolve an `extends` chain into one standalone file |
| `yahoo-auth` | log in once, by hand, and save the session |
| `yahoo-probe` | fetch one page and report what's in it |
| `yahoo-season` | download a real league-season |
| `yahoo-evaluate` | replay real seasons under two rule sets |

## Reading the verdict

The headline number is **decisive rate**: given that a rule fired, how often was its
swing bigger than the margin of the game it landed in. That is the direct formalisation
of "this rule decides matches". It beats a flip rate, which additionally requires the
rule to land on the losing side, and it stays comparable between rules that fire at
very different frequencies.

| decisive rate | verdict | |
|---|---|---|
| ≥ 50% | **AUTO-DECIDE** | when it fires, it is the game |
| 25–50% | **HIGH SWING** | dial the magnitude down |
| 10–25% | **SPICY** | adds variance without taking over |
| < 10% | **FLAVOR** | mostly cosmetic |

Plus a **LOTTERY TICKET** flag for rules firing in under 2% of team-weeks that decide
the game when they do — arguably the worst failure mode, and invisible to a flip rate.

Every rate carries a Wilson interval, because sample size is the whole problem: a rule
firing 6% of the time in one season gives four events.

**Size alone doesn't make a rule decisive — asymmetry does.** A thousand points to
every team's kicker moves every score enormously and changes no result. The tool
measures that correctly as no swing at all, and there's a test for it, because
"generous participation bonus" is a shape real house rules take.

## Writing rules

`rules/base_yahoo.yaml` is the league's current settings. A category is enabled by
giving it a value; anything omitted is switched off in Yahoo. Candidates use
`extends:` and list only what changes; `null` turns a category off. Chains are allowed
(`base` → `superflex` → `balanced`), applied root first.

Yardage goes under `yards_per_point`, in the units Yahoo's settings page asks for:

```yaml
yards_per_point:
  receiving_yards: 7.5   # "7.5 yards = 1 point", exactly as typed into Yahoo
```

There is no box for 0.1325 a yard, so a config written that way can't be transcribed.
Both spellings score identically; a category may not be given both ways.

Validation is deliberately strict, because a typo that silently scored zero would
corrupt an analysis while looking entirely plausible. Unknown categories are rejected
with a suggestion, bonuses are held to Yahoo's limits (three tiers, only on
passing/rushing/receiving yards), and the 26-category offensive cap is enforced.

For anything Yahoo can't express — a penalty past a threshold, a boost that depends on
the standings, a streak — write Python. `rules/house_2026.py` holds the current set:

```python
@rule("fg_long_bonus", positions=["K"])
def fg_long_bonus(ctx: RuleContext) -> float:
    """Big bonus for a long field goal."""
    made = sum(1 for y in ctx.line.events.get("fg_made_yards", ())
               if y >= ctx.param("min_yards", 50))
    return ctx.param("bonus", 50.0) * made
```

Two constraints in the rule context are deliberate:

- **Rules see teammates and opponents base-scored only**, never another rule's output,
  so rules are order-independent and there is no "which one ran first" class of bug.
- **`history` holds only completed weeks**, so a rule structurally cannot see the
  future — enforced by what's in the object, not by a convention asking nicely.

Prefer `.base` over `.total` when reading history for anything streak-shaped, or the
bonus ends up feeding the streak that earns it.

## Balancing positions

`balance` measures the average startable player at each position and solves for
increases that even them out — useful when a flex slot should be position-neutral.

```bash
uv run fantasy-tool balance --rules rules/superflex_qwr.yaml --premium none
```

It balances only the positions that actually compete for a flex slot. A position with
nothing but a dedicated slot doesn't need balancing: every team starts exactly one, so
scoring less is symmetric and costs nobody. That single observation is what let the
league's tight-end problem be solved by taking tight ends *out* of the flex rather than
by paying them more — Yahoo has no per-position multipliers, and tight ends are
out-produced by receivers on every shared receiving stat, so no native change can close
that gap.

## Real league history

Yahoo has no API access here, so lineups are scraped. Log in once, by hand:

```bash
uv run playwright install chromium
uv run fantasy-tool yahoo-auth                      # a browser opens; log in
uv run fantasy-tool yahoo-season 583648 --season 2024
```

Past seasons need that season's league id — Yahoo issues a new one at every renewal —
and live under a year-prefixed path.

Yahoo supplies **who started whom, and nothing else**. Every point is recomputed from
nflverse under whichever rules are being tested, which matters because the league's own
scoring changed year to year; the 2024 pages score categories the 2026 settings don't
have. That also means a real-season replay measures **scoring changes only**: what a
different *roster* would have produced is unknowable from lineups set under the old one.

Everything is cached to disk on first fetch, so re-parsing never re-downloads.

## Scoring conventions

Judgment calls that materially change how often a rule fires. First thing to check when
a number looks wrong.

**Points allowed** counts *all* points the opposing NFL team scored, including their
defensive and special-teams touchdowns. This matches Yahoo, and it surprises people: a
pick-six thrown by the quarterback your D/ST is facing still counts against your D/ST.

**Yards allowed** is total net yards: gross passing, less sack yardage, plus rushing.
nflreadpy stores `sack_yards_lost` as a *negative* number, so the natural-looking
`passing − sacks + rushing` silently adds it back and inflates every team by ~30 yards a
game. Pinned to a real box score in the tests.

**Fumbles lost** uses `fumbles_lost_total`, not the sum of the positional columns —
return-game and aborted-snap fumbles land only in the total.

**Points and yards allowed are scored as bands, not lookups.** Yahoo models each band
as its own on/off category, so they're stored as 0/1 indicators. Scoring is then a plain
sum of `multiplier × stat` with no tier machinery.

**Thirteen categories need play-by-play**: long *touchdowns* as distinct from long
plays, touchdowns split by how the ball was turned over, and drive-level defensive
stops. Derived in `sources/pbp.py` at sync time.

**Laterals break the obvious derivation of long touchdowns.** Goff to St. Brown for 1
yard, lateral to Williams for 41 and the score, is a 42-yard *passing* touchdown but not
a 40-yard *receiving* touchdown for St. Brown. Ball-carrier categories require the
player to have actually scored.

**A kicker is not confined to kicking categories.** Yahoo's Offense / Kickers headings
organise the settings page; they don't restrict who earns what. Fake field goals are in
the data — Boswell threw a touchdown, Sanders caught one, Elliott threw an interception.
The only real boundary is players versus team D/ST units.

**D/ST "Touchdown" means any defensive score.** nflverse's `def_tds` counts interception
returns only — it matched our pick-six count exactly, to the row — so it's rebuilt from
interception, fumble and blocked-kick returns. Kick and punt returns stay out; Yahoo
scores those separately.

**nflverse's own `fantasy_points_ppr` uses a narrower fumble rule than Yahoo**, summing
the positional columns and so missing return fumbles. That is the *only* permitted
disagreement in the scoring oracle, which checks every offensive player-week across
every synced season (~45,000 lines) and requires each mismatch to be explained by it.

## How the simulation is calibrated

A simulator that runs cleanly but produces a league behaving nothing like real fantasy
football gives confident, wrong answers. The headline metric is a rule's swing measured
against a typical margin of victory, so the margin has to be right.

| | target | why |
|---|---|---|
| median margin of victory | 20–25 | the denominator of every verdict |
| lineup efficiency | 85–90% | higher means hindsight, lower means noise |
| season points spread | 1.5–2× | best team vs worst |

All asserted in `tests/test_sim.py`. Four corrections were needed, each of which
produced a league that *looked* fine:

- **Draft on value over replacement, not raw points.** A quarterback outscores nearly
  everyone, but you start one and the waiver wire has another nearly as good. Ranking
  on raw value builds rosters with three quarterbacks and three receivers that then
  can't field a legal lineup.
- **Scale manager error to the data.** Draft value has a spread of about 3 points; noise
  was set at 14. The draft was effectively random. Both noise terms are now multiples of
  the observed spread, so they stay calibrated when the rules change.
- **The draft board must include breakouts.** Trimming by prior-season value alone
  excludes everyone who breaks out this year — precisely the players who make lineups
  good. Membership considers both seasons; draft *order* still uses only the prior one.
- **Managers know byes, not outcomes.** The stat feed only carries players who recorded
  something, so absence means "didn't produce", not "didn't play". Byes are derived
  exactly (a team is on bye iff its defense has no row).

Known gaps: no trades, no injury replacement beyond streaming, and real managers work
the waiver wire harder than simulated ones — measured against the league's own seasons,
the model under-scores by roughly 25%. Treat synthetic *magnitudes* as directional and
*comparisons* as sound; the diff is far more robust than the levels.

**Exploit resistance is tested.** A flex-heavy lineup invites hoarding one position, so
the draft can be told to do exactly that. Any rule set where hoarding beat a normal
draft would be one to reject: it would reward whoever noticed at the expense of everyone
who didn't.

## Layout

```
rules/
├── base_yahoo.yaml     the league's real current settings
├── superflex*.yaml     roster experiments
├── balanced*.yaml      position-balanced scoring
├── league_2026.yaml    the adopted rules, flat (generated by `flatten`)
└── house_2026.py       Python rule bodies

src/fantasy_tool/
├── cli.py              typer commands
├── model.py            StatLine, League, TeamWeek, History — the source-agnostic contract
├── stats.py            the registry: a text mirror of Yahoo's settings page
├── scoring.py          YAML rule sets and the base scorer
├── rules.py            the @rule registry and RuleContext
├── sim.py              two-pass season replay
├── analysis.py         counterfactual diff and the four metric families
├── balance.py          position value measurement and the solver
├── sweep.py            parameter sweeps
├── harness.py          league construction shared by evaluate and sweep
├── report.py           rich tables
├── store.py            sync / load persisted parquet
└── sources/
    ├── nfl.py          weekly tables -> StatLine (sync time only)
    ├── pbp.py          play-by-play categories (sync time only)
    ├── snaps.py        offensive snap counts (sync time only)
    ├── synthetic.py    draft, lineups, streaming
    └── yahoo/          auth, fetch, parse, season assembly
```

## Tests

```bash
uv run pytest
```

The most valuable ones are the bracket tests in `tests/test_analysis.py`. A contaminated
counterfactual produces plausible output rather than a crash, so: a null rule must
measure as exactly nothing, a rule paying both sides equally must flip nothing, and an
asymmetric rule worth a thousand points a kick must read as decisive.

Determinism is a contract and is checked across processes, not just within one — Python
randomises string hashing per run, and iterating a set of position names once made the
same seed produce different leagues in different processes.
