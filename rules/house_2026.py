"""Candidate house rules for 2026.

These are the ideas being tested, not settled rules. Every threshold and magnitude is
a parameter so `sweep` can ask "what if the penalty were -12 instead of -40" without
touching this file.

A rule returns a point delta: positive adds, negative subtracts, 0.0 means it didn't
fire. It gets one player-week at a time, with that manager's other starters, the
opposing starters, and every completed week available on the context.
"""

from fantasy_tool.rules import RuleContext, rule


@rule("fg_long_bonus", positions=["K"])
def fg_long_bonus(ctx: RuleContext) -> float:
    """Big bonus for a long field goal.

    Reads the exact yardage of every kick rather than Yahoo's 50+ bucket, so
    `min_yards` is freely tunable -- 50, 53, 55 -- without new stat columns. A week
    with two long kicks pays twice unless `cap` says otherwise.
    """
    minimum = ctx.param("min_yards", 50)
    each = ctx.param("bonus", 50.0)
    made = sum(1 for yards in ctx.line.events.get("fg_made_yards", ()) if yards >= minimum)
    if not made:
        return 0.0
    total = each * made
    cap = ctx.params.get("cap")
    return min(total, cap) if cap is not None else total


@rule("def_blowout_penalty", positions=["DEF"])
def def_blowout_penalty(ctx: RuleContext) -> float:
    """Heavy penalty when a defense gets run over.

    Stacks on top of the YAML points-allowed band, which already charges -4 at 35+.
    Note Yahoo counts every point the opposing NFL team scored, including their own
    defensive and special-teams touchdowns.
    """
    threshold = ctx.param("threshold", 40)
    if ctx.line.s("def_points_allowed") >= threshold:
        return float(ctx.param("penalty", -40.0))
    return 0.0


@rule("underdog_boost")
def underdog_boost(ctx: RuleContext) -> float:
    """Percentage boost for a manager well behind this week's opponent.

    The levelling lever, and impossible to express in Yahoo: it depends on the
    standings and on who you happen to be playing. Capped per player so it nudges
    rather than decides.
    """
    behind = ctx.history.record(ctx.opponent.team).wins - ctx.history.record(ctx.team.team).wins
    if behind < ctx.param("game_gap", 2):
        return 0.0
    boost = ctx.base * ctx.param("pct", 0.10)
    return min(boost, ctx.param("cap", 15.0))


@rule("position_premium")
def position_premium(ctx: RuleContext) -> float:
    """Extra points on a stat, for one position only.

    The one thing Yahoo genuinely cannot express: its categories apply to every player
    alike, so there is no way to pay a tight end more per catch than a receiver. That
    matters because tight ends are out-produced by receivers on every per-game
    receiving stat, so no shared category can close the gap between them -- raising
    receiving yards for everyone helps receivers more and widens it.

    Parameters are `<POSITION>_<stat>`, e.g. `TE_receiving_yards: 0.16` adds 0.16 a
    yard for tight ends on top of whatever the shared category pays.
    """
    total = 0.0
    for key, rate in ctx.params.items():
        position, _, stat = key.partition("_")
        if position == ctx.line.position and stat:
            total += float(rate) * ctx.line.s(stat)
    return total


@rule("hot_hand", positions=["QB", "RB", "WR", "TE"])
def hot_hand(ctx: RuleContext) -> float:
    """Flat bonus for a player on a scoring streak.

    Deliberately reads `.base` rather than `.total`: keying off the total would let
    the bonus count toward the streak that earns it.
    """
    weeks = int(ctx.param("weeks", 3))
    threshold = ctx.param("threshold", 20.0)
    recent = ctx.history.player_lines(ctx.line.player_id)[-weeks:]
    if len(recent) < weeks or any(scored.base < threshold for scored in recent):
        return 0.0
    return float(ctx.param("bonus", 5.0))


@rule("participation_trophy", positions=["QB", "RB", "WR", "TE"])
def participation_trophy(ctx: RuleContext) -> float:
    """A starter who took the field and scored nothing gets a floor instead of a zero.

    Requires at least one *offensive* snap, so it rewards showing up rather than being
    inactive. Special teams doesn't count, which is why the store carries offensive
    snaps separately -- the stat feed omits players who recorded nothing, making
    "played and did nothing" otherwise indistinguishable from "didn't play".

    The floor is a fixed number per position rather than that week's average. A fixed
    value is a lookup instead of a weekly tally of everyone's lineups, it tells a
    manager in advance what the floor is worth, and it avoids the odd incentive of
    having your own bonus rise when your opponent's players do well.
    """
    if ctx.base != 0.0 or ctx.line.s("offense_snaps") < 1:
        return 0.0
    return float(ctx.param(f"{ctx.line.position}_value", 0.0))
