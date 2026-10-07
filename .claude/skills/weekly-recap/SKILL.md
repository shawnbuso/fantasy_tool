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

**Pass `--week N` explicitly.** Omitting it uses whatever week Yahoo says it is on, which
is right on a Monday night and wrong by Tuesday — the week rolls over, and week 4 was
fetched as an unplayed week 5: every score 0.00, every starter in `unplayed`. The script
prints the week it thinks it is ("Yahoo says this is week 5"), so read that line. Also
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

**A tiny snap share on a well-projected starter is a story, not a curiosity.** The trophy
record carries `offense_snaps` and `offense_pct`. A starter projected for double digits who
played ~20% of the offensive snaps or fewer either got hurt or got benched, and which one it
was is usually the actual joke — a week 4 trophy player managed 6 snaps against a 17.35
projection because he left the game injured, and **nothing in the JSON says so.** There is no
injury or status field at all, so this cannot be looked up in the file: search the week's
news or ask. Reporting the snap count without the reason reads as a shrug.

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

1. Team            record    PF     all-play
...all ten...
```

**The standings block carries the all-play column**, and the closing paragraph says in
passing what all-play is — the record you'd have against all nine other teams every week.
Week 1 had the column, weeks 2 to 4 silently dropped it, and it was missed the week that
luck was the whole frame. `to_email_html.py` picks up the fifth column and labels it.

### Voice

Witty, tongue-in-cheek, affectionate. The target is a family group text, not a Deadspin
column — these people see each other at Christmas.

**Read `recaps/week-01-recap.md` before drafting.** It is the benchmark the league liked
best, and week 3 drew the verdict "lost a bit of its edge since week 1 — very data-heavy
and less fun and jokey." The drift is gradual and invisible from inside a single week, so
the comparison has to be made deliberately. What week 1 had and week 3 had lost:

- **First person.** Week 1: "Full disclosure: I write this recap and I also posted the
  highest score in the league, and I'd like that noted as a coincidence." … "My defense
  scored negative two and I won by 46." Week 3 retreated into the third person — "the
  person writing the recap put up 218.31", "he also left Jeremiyah Love's 24.78 on his own
  bench" — which reads as arch and administrative. **The narrator is a cousin at the back
  booth, not a wire service.** Say *I*, say *my*, and address the league directly: "Make of
  that what you will."
- **A turn after every number.** Week 1: "Ja'Marr Chase turning in 3.60 against a 24.54
  projection — a line so quiet that starting the Chargers defense instead would have cost
  him 2.6 points." Week 3: "projected for 165.94, scored 129.90, and lost by
  88.41." Identical number *count*; one has a joke in it. See the budget rule below.
- **Jokes with a second beat.** "His kicker scored 1.00. His defense also scored 1.00,
  which at least shows commitment to a theme." The flat fact, then the reframe.

The craft rules themselves:

- **Four to five sentences per matchup, and lean long rather than short.** Tested
  against the league's own judgment: given the same joke in a three-sentence and a
  five-sentence version, the longer one won nearly every time. Brevity is not the
  virtue here — a joke needs room on both sides of it.
- **Numbers are not a budget.** A number doing *joke* work is free; a number doing
  *inventory* work is expensive. **The count was never the problem — week 1 and week 3
  carry about the same density, and only week 3 reads like a spreadsheet.** The test is
  per-number and mechanical: after each figure, is there a turn? A consequence, a reframe,
  a counterfactual, a joke? Three numbers in a row with no turn between them is a ledger
  entry, however true each one is. "Starting the Chargers defense instead would have cost
  him 2.6 points" needs three figures and earns all three, because the precision is the
  joke — it implies somebody actually ran the counterfactual. Three players listed with
  their scores and nothing else spends the same three figures on inventory nobody can
  picture. Ask what each number is *for*, never how many
  there are.
- **Never write a clause you cannot restate as a claim.** This is the most expensive habit
  in the job and week 4 produced three of them in one draft, each drawing the same question
  back — *what does that mean?*
    - "beat his son-in-law by 21.56, **which settles one in-law question and immediately
      raises another**" — there was no first question and no second one.
    - "tenth in scoring to sixth in a week, **which is the fastest turnaround this format
      allows**" — not merely empty but false; tenth to first would be faster.
    - "he has benched his own team's players twice and won both times, **and I would like
      somebody to explain to me why that isn't just strategy**" — a shrug wearing a joke's
      clothes.
  All three have the *rhythm* of a reframe and assert nothing, which is exactly how the
  "turn after every number" rule above gets gamed: the rule is satisfied by anything shaped
  like a punchline. **The test: say out loud what the clause claims. If you can't, it claims
  nothing.** Watch for "which is the most/fastest/only X that Y allows", "make of that what
  you will", and "which raises the question" — these read as precision and are decoration.
  A comparison pass will never catch one, because an empty clause contains no figures.
- **The fix is usually to invent a claim, not to delete the clause.** The third example above
  was replaced with "**I want to know who he's got feeding him info from inside the locker
  room**", and that is the whole lesson in one edit: don't ask the reader to find the
  situation funny, assert something specific that is. A named conspiracy beats a raised
  eyebrow every time.
- **Build one joke that runs across the recap.** The locker-room informant was invented in
  the first matchup and cashed four matchups later, when a second manager did the same thing
  — "does he have the same informant?" — turning a one-off into a through-line for eight
  words. The league reads to the end if the end rewards it. One per recap, invented
  early and paid off late, and it costs nothing because the week supplies the coincidence.
- **Put the punch word last.** "She didn't have to get good, in other words — she just had to
  get better than me" was rewritten to "In other words, she didn't have to get good — she
  just had to get better than me." Identical words; the self-deprecation moved to the final
  position, where it lands. Qualifiers and asides go at the front of the sentence.
- **Address the award recipient directly.** Award blurbs are the one place second person
  works: "the cards all around you looked better, but you've gotta play the one you're
  handed" beats any third-person description of the same bad luck, and for the consolation
  awards warmth is the entire job. It also beats reaching for lore — if the award's *name*
  already carries the reference, explaining it again is redundant.
- **Relationship labels for the first reference, names after that.** "My dad beat my
  brother-in-law" is good once; the next sentence needs the name, because by then "he" could
  be either of them. Any paragraph holding two people needs at least one name per person.
- **A punchline needs a sentence on one side of it.** The reliable shape is setup then
  landing, or a flat fact that a following line retroactively reframes. A bare short
  sentence as the *closer* usually dies — it reads as trailing off. The same sentence
  as a first beat, with one more after it, lands.
- **Be specific.** "Left 66 on the bench" is funny. "Had a rough week" is not. Every
  barb should be anchored to something real out of the JSON.
- **Never explain the football.** This league shares a home team and watched the games you
  are describing. Week 3's draft spent four sentences establishing a famous backup
  quarterback's championship run — the injury that opened it, the date, the yardage, the
  final score, the MVP — and all of it was cut down to a parenthetical "(see: Super Bowl
  LII)" before sending. The note back was that it should be "tongue-in-cheek, if you know
  you know, and less overt." **A shared memory referenced glancingly is a joke; the same
  memory explained is a Wikipedia summary**, and it tells the room you think they don't
  remember their own championship. Land it in a clause and move on.
- **The dead are comedy, not elegy.** The dossier's departed relatives are in there because
  of the ridiculous things they did, and that is exactly how to use them. What does not
  work is the affectionate-memorial register: week
  3 opened a matchup by noting which late relatives a family tradition had been *inherited
  from*, and the whole clause was cut before sending: it added nothing except reminding the
  league that those people are gone, which reads as "kind of a bummer." Before invoking
  someone who has died, check what the subtext of the sentence actually is. If it is
  *this person is gone*, cut it. If it is *this person did something ridiculous*, keep it.
- **Rigor in the checking, looseness in the telling.** Section 4 demands every superlative
  name its window and every comparison be computed, and week 3 let that discipline leak
  into the prose: "The honest footnote is that it wouldn't have mattered", "so the bench
  isn't really the story", "There was no version of Sunday where this went differently",
  scope qualifiers stacked into clauses nobody asked for. That is the voice of someone
  showing their work. **Verify like an auditor, write like a cousin.** Qualify a claim once,
  in the sentence that makes it, then get out.
- **Explain the jargon or drop it.** "Within 3.54 points of perfect" means nothing to
  most of the league — a perfect lineup is the best legal lineup a manager already
  owned, scored after the fact. Either say so in passing or use a phrasing that carries
  itself.
- **Everyone gets hit, including whoever is writing.** Self-deprecation is what buys the
  license to go after everyone else, so never let the author's own loss pass quietly.
- **Use the family.** Every matchup in this league has a relationship behind it —
  brothers, father-son, in-laws. The dossier has the map. A recap that ignores it has
  thrown away the entire premise.
- **Vary the recurring bits.** The dossier lists the evergreen jokes. Using all of them
  every week kills them inside a month. Two or three, and rotate.
- **Punch at decisions, not at people.** Bad lineup calls, benched studs, draft picks
  made by someone's eight-year-old. Never anything real.
- **American spelling, always.** This is an American family in an American football
  league: *offense*, *defense*, *color*, *license*, *humor*, *neighborhood*, *toward*,
  *canceled*, *gray*. British forms read as an affectation from a narrator who is
  supposed to be one of them, and "74% of Arizona's offence" is the kind of thing that
  gets noticed immediately. (The repo's own source uses British spellings in places;
  that is the codebase's business and not the recap's.)
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
one recap — the rest of the humor has to come from what actually happened.

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

Pass `--also` with every earlier week's JSON, or legitimate callbacks to September
figures read as fabricated in November. Note the cost: the allowed set grows with each
week added (416 values for one week, 713 for two), and the false-pass rate on random
plausible figures rose from 2.5% to 4.0% between them. It will keep climbing, so the
comparison pass below matters more as the season goes on, not less.

It accepts player points and projections, team totals, margins, bench figures, snap
counts and real NFL game scores, plus sensible roundings — "lost by 46" for 46.12 is
fine. Anything else is printed. A flagged number is not automatically wrong: a genuine
comparison like "starting the Chargers defense instead would have cost him 2.6 points"
is computed from two real figures and will be flagged every time. **Recompute each
flagged line from the JSON and confirm it.** An empty list is the only result that needs
no thought.

**Pass two — the comparison pass. Write it out and show the user.** The linter validates
*figures*, not *comparisons*, and comparisons are where this actually goes wrong: week 2
shipped a draft claiming two tight ends outscored several teams' entire lineups (they
scored 67.00; the lowest team total was 145.48) and calling someone the best lineup
manager in the league when he was third. Both numbers in those sentences were real.

So before publishing, list every comparative claim in the draft, write a one-line check
for each against the JSON, run them, and print the result. Not "I checked" — the actual
table, with the losing values shown next to the winning one:

```
OK   best start of the week            (55.67), next (47.00)
OK   third-fewest points               [268.56, 302.96, 315.60, 325.53]
>>>  best lineup in the league         season avg: 98.1, 95.9, 95.8  <- claimed team is 3rd
```

The third line there is a real error the pass caught after the figures had already come
back clean. **Scope is the usual failure**, and it has now caused three separate corrections.
"Best in the league" is a season claim in a power-ranking or standings section and a
weekly claim in a matchup writeup, and a sentence true of one gets pasted into the other.

**Every efficiency, ranking or superlative must name its window in the sentence** —
*this week*, *across the two weeks*, *on the season*. An unqualified one is a bug even
when the underlying number is right: week 2 opened with "the worst-managed lineup won"
(a weekly figure, unattributed) and ranked another team "second most efficient in the
league"
(a season figure) four paragraphs apart, and the two read as a flat contradiction. Both
were true. Neither said so.

Naming the team also helps. "The worst-managed one won by 37" makes the reader guess;
"<team> started 81.2% and won by 37" cannot be misread.

**An ordinal must also say what it ranks.** "Tenth to second in a week" sounds like a
climb up the standings; it meant lowest score in the league one week and second-highest
the next, while the team sat eighth in the table the whole time. Weekly scoring rank,
standings position, all-play and efficiency are four different orderings and a bare
ordinal could be any of them. Say which — "lowest score in the league", "eighth in the
standings", "13-5 against the field" — or don't use the number. In week 1 a draft went out saying someone's bench outscored four
teams' entire lineups; the bench figure was correct and the comparison was invented, and
the linter would have passed it. So every superlative and every comparison gets computed
from the data, never estimated:

> highest · lowest · best · worst · most · fewest · closest · biggest · "more than N
> teams" · "the only" · "first since" · "outscored X" · any ranking or ordinal

Write the one-line check and run it. `max()` over a list is five seconds and it is the
difference between a recap the league trusts and one somebody fact-checks in the group
chat. Beware especially of claims that were true in an earlier draft — Monday night
moves scores, and "the fewest points in the league" can change hands after you've
written it.

## 5. Save it and update the dossier

Write the recap to `.claude/skills/weekly-recap/recaps/week-NN.txt` (gitignored).

**Then ask for what actually went out, and save it as `week-NN-sent.txt`.** The recap gets
edited before sending, and those edits are the only direct read on the voice available —
the week 3 diff produced four separate corrections that no amount of self-review had
caught. Diff sent against drafted before writing the next week. If you haven't been told,
ask; if it went out unchanged, note that, because that is information too.

Then make three updates to the dossier — all of them, every week, or the anti-repetition
machinery quietly stops working:

1. **The bit ledger** — mark the week against every bit you used.
2. **The season arcs** — add this week's numbers to the running totals.
3. **The season log** — 3-5 terse lines: who won, what the story was, what's worth
   paying off later. Notes to self, not prose.
