---
name: weekly-recap
description: Write the weekly fantasy football recap for the family Yahoo league — matchup summaries, power rankings, awards and standings, in a humorous tongue-in-cheek voice. Use when asked for the weekly recap, a week's writeup, or "recap week N".
---

# Weekly recap

Pulls a week of the league off Yahoo and writes it up as something people will
actually read on a Tuesday morning.

## Before anything else: read the dossier

`league-context.local.md`, next to this file, holds the league's history and a profile
of every manager — who is related to whom, where each team name comes from, and the
angle each person is funniest from. **It is gitignored and must stay that way.** Never
quote it into a commit, a PR, or anything that leaves the machine.

If it's missing, stop and say so. A recap written without it is ten anonymous fantasy
teams and is not worth sending.

## 1. Get the data

The league id, the manager profiles, and everything else specific to this league live in
`league-context.local.md` beside this file, which is gitignored. Read the id from there;
it is deliberately not written down in a tracked file.

```bash
uv run python .claude/skills/weekly-recap/scripts/fetch_week.py \
    --league $LEAGUE_ID --out .claude/skills/weekly-recap/recaps/week-NN.json
```

Keep every week's JSON — the directory is gitignored, and season-long stats (who has
been unlucky all year, whose bench keeps outscoring their lineup) need the history.

Omit `--week` and it uses whatever week Yahoo is on, which is the right answer when
run on a Monday night or Tuesday. Pass `--week N` to redo an earlier one, and
`--season YYYY --week N --no-refresh` to read a finished season out of the cache.

Ten pages at 1.5s apiece, so expect ~20 seconds.

Keep the JSON next to the recap — `verify_numbers.py` needs it in step 4, and the
season-long stats need the history.

**If the session has expired**, the script says so. Ask the user to run
`uv run fantasy-tool yahoo-auth` — it opens a browser for a manual login and cannot be
automated. Yahoo sessions last days to weeks, so this comes up every month or so.

### What comes back

Per team: `points`, `raw_points`, `adjustment`, `projected`, `optimal`,
`left_on_bench`, `should_have_started`, `record`, `place`, `season_points`, and every
starter and bench player with their points, projection and the NFL game they came out
of. Per matchup: both teams, the margin, and the winner. Plus `week_high`, `week_low`
and `league_average`.

**`points` is Yahoo's official score, not a sum of the roster.** The league runs a
**participation trophy**: an offensive starter who takes at least one snap and scores
exactly 0.00 is credited with 10 points instead. Special teams is excluded, so kickers
and defenses never qualify. Yahoo can't express the rule, so the commissioner posts it
by hand and
only the displayed score reflects it.

`raw_points` is what the starters actually produced, `adjustment` is the gap, and
`participation_trophy` names the players who earned it — so the recap can say *which*
zero turned into ten. `adjustment_explained` is the reconciliation: when it's false,
the adjustment isn't the trophy rule and you should ask rather than guess.

`decided_by_adjustment` on a matchup means the trophy swung the result. That is always
the lead story of the week — a game won by a player who did nothing at all is the
funniest thing this league can produce.

`left_on_bench` is the difference between the raw score and the best legal lineup that
manager owned. It is the most reliable joke in the file — mine it every week. It
deliberately ignores the adjustment, which nobody earned.

**A bench player's score is not what he cost his manager.** Starting him means sitting
somebody, so the real loss is his points *minus whoever he'd have replaced*. Isaiah
Likely scored 39.50 on a bench in week 1; the damage was 33.03, because the spot he'd
have taken was already producing 6.47. Quoting the raw bench score as "points left on
the bench" overstates it every time, and the sentence reads as true because every figure
in it is real. (`left_on_bench` itself is safe — it's a full lineup solve and already
nets this out. It's the per-player claims that go wrong.)

`bench_swings` does the arithmetic: for each bench player it gives `net_gain`, the
starter he'd have replaced (`instead_of`), and the slot. **Cite `net_gain`, or name both
players.** Watch the second trap too — a player is blocked by the *cheapest slot he was
eligible for*, which is usually a flex rather than someone at his own position. Week 1's
draft had Likely stuck behind a tight end when the flex was the real answer, which
understated the damage by half and named the wrong teammate.

**A bench player's score is not what he cost his manager.** Starting him means sitting
somebody, so the loss is his points *minus whoever he'd have replaced*. Isaiah Likely
scored 39.50 on a bench in week 1; the actual damage was 33.03, because the flex spot he
would have taken was already producing 6.47. Quoting the raw 39.50 as "points left on
the bench" overstates it every time, and the sentence reads as true because every figure
in it is real.

`bench_swings` does this arithmetic for you: for each bench player it gives `net_gain`,
the starter he'd have replaced (`instead_of`), and which slot. **Cite `net_gain`, or name
both players.** "Likely's 39.50 behind Brown's 6.47 in a flex" is honest; "Likely's 39.50
on the bench" is not. Watch for the second trap too — a player is blocked by the
*cheapest slot he was eligible for*, which is often a flex, not the starter who shares
his position. Week 1's draft had Likely stuck behind a tight end when the flex was the
real answer, and understated the damage by half.

`still_playing` lists starters whose NFL game is in progress; `unplayed` lists starters
with no score at all. If either is non-empty the week isn't final — say so rather than
reporting a result that may not survive the fourth quarter.

## 2. Check the week's NFL news

Search for what actually happened — the injuries, the upsets, the story everyone
watched. One or two real references tie the league to the wider week and stop the
recap reading like a spreadsheet. Don't force it; a recap with no NFL hook is better
than one with a strained one.

## 3. Write it

Format is **email / group text**: plain text with light structure. No markdown
headers, no tables that need a monospace font to parse. Section dividers in caps.

```
Subject: <league name> — Week N Recap

[One or two sentences setting up the week.]

=== THE MATCHUPS ===

Team A 142.60 — Team B 88.12
[4-5 sentences.]

...one block per matchup, five in all...

=== POWER RANKINGS ===

1. Team (record) — [one line]
...all ten...

=== AWARDS ===

[3-4 awards, named out of the league's own lore rather than generic ones.]

=== STANDINGS ===

1. Team            record    PF
...all ten...
```

### Voice

Witty, tongue-in-cheek, affectionate. The target is a family group text, not a Deadspin
column — these people see each other at Christmas.

- **Four to five sentences per matchup, and lean long rather than short.** Tested
  against the league's own judgment: given the same joke in a three-sentence and a
  five-sentence version, the longer one won nearly every time. Brevity is not the
  virtue here — a joke needs room on both sides of it.
- **Numbers are not a budget.** A number doing *joke* work is free; a number doing
  *inventory* work is expensive. "Starting the Chargers defense instead would have cost
  him 2.6 points" needs three figures and earns all three, because the precision is the
  joke — it implies somebody actually ran the counterfactual. Three players listed with
  their scores and nothing else spends the same three figures on inventory nobody can
  picture. Ask what each number is *for*, never how many
  there are.
- **A punchline needs a sentence on one side of it.** The reliable shape is setup then
  landing, or a flat fact that a following line retroactively reframes. A bare short
  sentence as the *closer* usually dies — it reads as trailing off. The same sentence
  as a first beat, with one more after it, lands.
- **Be specific.** "Left 66 on the bench" is funny. "Had a rough week" is not. Every
  barb should be anchored to something real out of the JSON.
- **Explain the jargon or drop it.** "Within 3.54 points of perfect" means nothing to
  most of the league — a perfect lineup is the best legal lineup a manager already
  owned, scored after the fact. Either say so in passing or use a phrasing that carries
  itself.
- **Everyone gets hit, including whoever is writing.** Self-deprecation is what buys the
  licence to go after everyone else, so never let the author's own loss pass quietly.
- **Use the family.** Every matchup in this league has a relationship behind it —
  brothers, father-son, in-laws. The dossier has the map. A recap that ignores it has
  thrown away the entire premise.
- **Vary the recurring bits.** The dossier lists the evergreen jokes. Using all of them
  every week kills them inside a month. Two or three, and rotate.
- **Punch at decisions, not at people.** Bad lineup calls, benched studs, draft picks
  made by someone's eight-year-old. Never anything real.
- **No gendered language for the managers.** Not "a man posted the week's best score",
  not "the most football-literate man in the league", not "the guy who wrote the rules".
  The league is not all men, and those constructions quietly say it is. Reach for
  *manager*, *somebody*, *whoever*, *anyone*, or name the team. Pronouns for a specific
  person whose pronouns you know are fine — the problem is the generic noun, which
  treats one kind of owner as the default and everyone else as the exception.
- **Never suggest anyone isn't paying attention or isn't trying.** A standing
  instruction, and it applies hardest to the managers the standings already embarrass.
  Roast results, not engagement. If a joke only works because someone was asleep at the
  wheel, cut it.

### Not repeating yourself for seventeen weeks

This is the hardest constraint in the job and it needs managing deliberately, not
hoped through. A recap that exhausts its material in September is worse than no recap
by Halloween. Four mechanisms, all in the dossier:

**The bit ledger.** A table of every recurring joke with the week it last appeared.
Read it before writing. A bit used last week is banned this week and shouldn't return
inside three. Update the table when you're done. At most **two** evergreen bits in any
one recap — the rest of the humour has to come from what actually happened.

**A different lens each week.** Don't structure every recap the same way. Rotate the
angle the week is viewed through, and don't reuse one inside a month:

> benches and what was left on them · projections vs reality · the kickers and
> defenses nobody thinks about · the waiver wire · family subplots · the all-play
> standings · luck and who deserved better · the trades nobody made · rookies ·
> the injury report · what the simulator would have predicted

**Season arcs over one-off jokes.** A punchline is spent once; a running counter gets
funnier every time the number grows. The dossier tracks several — someone's cumulative
bench points, a head-to-head household record, trophies awarded. Feed them every week
and cash them in late. By week 12 the number *is* the joke and needs no setup.

**Stats that only exist later.** `all_play` and `efficiency` in the JSON generate fresh
angles indefinitely, and accumulate into things no single week can show — who's been
unlucky all season, who keeps winning with the third-worst score. Save each week's JSON
(see below) so the season's shape is always available.

### Callbacks

Read the `## Season log` at the end of the dossier before writing, and pick up whatever
is still running — a rivalry, a losing streak, a prediction that aged badly. Continuity
is what makes people look forward to it, and it's material that costs nothing because
the season generates it for free.

## 4. Verify every number before it goes out

Two passes, because they catch different failures.

**Pass one — the figures.** Every number in the prose must trace to something in the
JSON:

```bash
uv run python .claude/skills/weekly-recap/scripts/verify_numbers.py \
    --recap .claude/skills/weekly-recap/recaps/week-NN.md \
    --data  .claude/skills/weekly-recap/recaps/week-NN.json
```

It accepts player points and projections, team totals, margins, bench figures, snap
counts and real NFL game scores, plus sensible roundings — "lost by 46" for 46.12 is
fine. Anything else is printed. A flagged number is not automatically wrong: a genuine
comparison like "starting the Chargers defense instead would have cost him 2.6 points"
is computed from two real figures and will be flagged every time. **Recompute each
flagged line from the JSON and confirm it.** An empty list is the only result that needs
no thought.

**Pass two — the claims, which no script can check.** The linter validates *figures*,
not *comparisons*. In week 1 a draft went out saying someone's bench outscored four
teams' entire lineups; the bench figure was correct and the comparison was invented, and
the linter would have passed it. So every superlative and every comparison gets computed
from the data, never estimated:

> highest · lowest · best · worst · most · fewest · closest · biggest · "more than N
> teams" · "the only" · "first since" · any ranking or ordinal

Write the one-line check and run it. `max()` over a list is five seconds and it is the
difference between a recap the league trusts and one somebody fact-checks in the group
chat. Beware especially of claims that were true in an earlier draft — Monday night
moves scores, and "the fewest points in the league" can change hands after you've
written it.

## 5. Save it and update the dossier

Write the recap to `.claude/skills/weekly-recap/recaps/week-NN.txt` (gitignored), then
make three updates to the dossier — all of them, every week, or the anti-repetition
machinery quietly stops working:

1. **The bit ledger** — mark the week against every bit you used.
2. **The season arcs** — add this week's numbers to the running totals.
3. **The season log** — 3-5 terse lines: who won, what the story was, what's worth
   paying off later. Notes to self, not prose.
