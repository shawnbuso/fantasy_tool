"""Check every number in a drafted recap against the week's data.

Written after a draft went out claiming someone's bench outscored four teams' entire
lineups. The figure itself was right; the comparison was invented. This catches the
other failure mode -- a number that matches nothing in the data at all -- which is the
one a human reader cannot catch, because a wrong decimal reads exactly like a right one.

Every number in the prose must trace to something real: a player's points or
projection, a team total, a margin, a bench figure, a snap count, or an NFL game score.
Anything that doesn't is printed for review. Rounded forms are accepted, because "lost
by 46" is a fair way to say 46.12.

    uv run python .../verify_numbers.py --recap week-01-recap.md --data week-01.json

It cannot check *claims* -- "the highest score in the league" is a comparison, not a
number. Those still have to be computed. See SKILL.md.
"""

import argparse
import json
import re
from pathlib import Path

# Weeks, ranks, list markers, ordinals, years. Small bare integers carry no risk.
SAFE_INTEGERS = set(range(21)) | {2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026}
NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w%])")


def allowed_values(data: dict) -> dict[float, str]:
    """Every number the recap is entitled to use, and where it came from."""
    found: dict[float, str] = {}

    def add(value, source: str) -> None:
        if value is None:
            return
        number = round(float(value), 2)
        found.setdefault(number, source)

    for team in data["teams"]:
        name = team["team_name"]
        for field in (
            "points",
            "raw_points",
            "adjustment",
            "projected",
            "optimal",
            "left_on_bench",
            "efficiency",
            "season_points",
        ):
            add(team.get(field), f"{name}.{field}")
        # What a bench player was actually worth, and who he'd have replaced.
        for swing in team.get("bench_swings", []):
            add(swing["net_gain"], f"{swing['name']} net gain over {swing['instead_of']}")
            add(swing["their_points"], f"{swing['instead_of']} points")
        for trophy in team.get("participation_trophy", []):
            for field in ("offense_snaps", "targets", "receptions", "carries"):
                add(trophy.get(field), f"{trophy['name']}.{field}")
            if trophy.get("offense_pct") is not None:
                add(round(trophy["offense_pct"] * 100), f"{trophy['name']}.snap share")
        for player in team["starters"] + team["bench"]:
            add(player["points"], f"{player['name']} points")
            add(player["projected"], f"{player['name']} projected")
            # "Final W 59-37 @Car" -- real NFL scores are fair game in a recap.
            for score in re.findall(r"(\d+)-(\d+)", player.get("game", "")):
                add(int(score[0]), f"{player['name']} game score")
                add(int(score[1]), f"{player['name']} game score")

    for matchup in data["matchups"]:
        add(matchup["margin"], "margin")
        add(matchup["combined"], "combined")
    add(data.get("league_average"), "league average")

    # Sums and gaps a recap legitimately computes from two real figures.
    totals = [t["points"] for t in data["teams"]]
    for i, one in enumerate(totals):
        for other in totals[i + 1 :]:
            add(round(abs(one - other), 2), "gap between two teams")
    snaps = [x for t in data["teams"] for x in t.get("participation_trophy", [])]
    if snaps:
        add(sum(x.get("offense_snaps") or 0 for x in snaps), "total trophy snaps")
        add(sum(x.get("targets") or 0 for x in snaps), "total trophy targets")
    for team in data["teams"]:
        add(round(team["raw_points"] + team["left_on_bench"], 2), f"{team['team_name']} optimal")
    return found


def matches(number: float, allowed: dict[float, str]) -> str | None:
    """Accept an exact value, or a sensible rounding of one."""
    for value, source in allowed.items():
        if abs(value - number) < 0.005:
            return source
        # "lost by 46" for 46.12, "403 combined points" for 403.38, "owned 183" for 183.30
        if number == float(int(value)) or number == float(round(value)):
            return f"{source} (rounded from {value})"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recap", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()

    data = json.loads(args.data.read_text())
    allowed = allowed_values(data)
    text = args.recap.read_text()
    # The notes section is addressed to the author, not the league, and cites
    # before-and-after figures that no longer exist in the current data.
    text = text.split("## Notes for you")[0]

    unaccounted: list[tuple[str, str]] = []
    for line in text.split("\n"):
        for token in NUMBER.findall(line):
            number = float(token)
            if number.is_integer() and int(number) in SAFE_INTEGERS:
                continue
            if matches(number, allowed) is None:
                unaccounted.append((token, line.strip()[:90]))

    if not unaccounted:
        print(f"Every number in {args.recap.name} traces to the data.")
        return
    print(f"{len(unaccounted)} number(s) in {args.recap.name} trace to nothing in the data:\n")
    for token, line in unaccounted:
        print(f"  {token:<10} {line}")
    print(
        "\nEach one is either a typo or a figure you worked out yourself. Justify every\n"
        "line above before sending -- recompute it from the JSON and confirm it. An\n"
        "empty list is the only state that needs no thought."
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
