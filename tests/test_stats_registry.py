"""The registry must stay in lockstep with what the store actually contains."""

import polars as pl
import pytest

from fantasy_tool import store
from fantasy_tool.sources.nfl import POINTS_ALLOWED_BANDS, YARDS_ALLOWED_BANDS
from fantasy_tool.stats import BONUS_ELIGIBLE, STAT_BY_KEY, STATS, suggest

SEASON = 2024


@pytest.fixture(scope="session")
def frame() -> pl.DataFrame:
    store.sync([SEASON])
    return store.load_frame(SEASON)


def test_keys_are_unique() -> None:
    assert len(STAT_BY_KEY) == len(STATS)


def test_every_supported_stat_has_a_real_column(frame: pl.DataFrame) -> None:
    """A registry entry pointing at a missing column would silently score zero."""
    for stat in STATS:
        if stat.supported:
            assert stat.column in frame.columns, f"{stat.key} -> {stat.column}"


def test_unsupported_stats_are_deliberate() -> None:
    """Anything unscoreable must say why, so it can't be mistaken for an oversight."""
    for stat in STATS:
        assert stat.supported or stat.needs_pbp, stat.key


def test_bonus_categories_exist() -> None:
    for key in BONUS_ELIGIBLE:
        assert key in STAT_BY_KEY
        assert STAT_BY_KEY[key].supported


def test_band_categories_match_the_extractor() -> None:
    """Yahoo's points/yards-allowed rows and our indicator columns must line up."""
    for name, _, _ in POINTS_ALLOWED_BANDS + YARDS_ALLOWED_BANDS:
        assert name in STAT_BY_KEY, name
        assert STAT_BY_KEY[name].column == name


def test_bands_are_mutually_exclusive(frame: pl.DataFrame) -> None:
    """Exactly one band fires per defense-week, or scoring double-counts."""
    defenses = frame.filter(pl.col("position") == "DEF")
    for bands in (POINTS_ALLOWED_BANDS, YARDS_ALLOWED_BANDS):
        names = [name for name, _, _ in bands]
        assert (defenses.select(names).sum_horizontal() == 1).all()


def test_positions_are_sensible() -> None:
    assert STAT_BY_KEY["fg_made_50_"].positions == frozenset({"K"})
    assert STAT_BY_KEY["pa_35_plus"].positions == frozenset({"DEF"})
    assert "QB" in STAT_BY_KEY["passing_yards"].positions


def test_typo_suggestions() -> None:
    assert "receiving_yards" in suggest("recieving_yards")
    assert suggest("zzzzzzzz") == ""
