"""Verification for the Yahoo scraper's fetching layer.

Everything here runs without a network or a session. What can be tested before we
have real markup is the machinery around the parser: that URLs point at the right
season, that a cached page is never re-fetched, and above all that a login redirect
fails loudly instead of being parsed as if it were a league.
"""

from pathlib import Path

import pytest

from fantasy_tool.sources.yahoo import auth, fetch

LEAGUE = "786986"


# --------------------------------------------------------------- urls


def test_current_season_has_no_year_prefix() -> None:
    assert fetch.league_url(LEAGUE) == "https://football.fantasysports.yahoo.com/f1/786986"


def test_past_seasons_are_year_prefixed() -> None:
    """Yahoo archives past seasons under a year path -- and a different league id.

    A league gets a new id every time it renews, so the season and the id have to
    travel together; using this year's id with last year's path finds nothing.
    """
    assert fetch.league_url("123", season=2019) == (
        "https://football.fantasysports.yahoo.com/2019/f1/123"
    )


def test_pre_2012_plus_leagues_use_f2() -> None:
    assert "/2010/f2/123" in fetch.league_url("123", season=2010, game="f2")


def test_matchup_url_carries_team_and_week() -> None:
    url = fetch.matchup_url(LEAGUE, team_id=4, week=7, season=2023)
    assert url.endswith("/2023/f1/786986/4/matchup?week=7")


# --------------------------------------------------------------- guard rails


def test_login_redirect_is_an_error_not_data() -> None:
    """The single most important check in this module.

    Yahoo answers an unauthenticated request with a redirect to login rather than an
    error code. Parsed rather than refused, that produces a league where every lineup
    is empty -- which looks exactly like a real league whose owner forgot to set
    lineups, and would quietly corrupt a season of results.
    """
    with pytest.raises(fetch.SessionExpired, match="yahoo-auth"):
        fetch.check_response("https://login.yahoo.com/?done=...", "<html>Sign in</html>")


def test_missing_page_is_reported_as_missing() -> None:
    with pytest.raises(FileNotFoundError):
        fetch.check_response(
            "https://football.fantasysports.yahoo.com/f1/9999",
            "<html>The document you requested was not found on this server.</html>",
        )


def test_a_real_page_passes() -> None:
    fetch.check_response(
        "https://football.fantasysports.yahoo.com/f1/786986/1/matchup?week=1",
        "<html><table><tr><td>Josh Allen</td></tr></table></html>",
    )


# --------------------------------------------------------------- caching


class _FakeResponse:
    def __init__(self, url: str, text: str) -> None:
        self.url, self.text = url, text


class _FakeClient:
    """Stands in for httpx so the cache can be tested without a network."""

    def __init__(self, html: str = "<html><table>ok</table></html>") -> None:
        self.html = html
        self.calls: list[str] = []

    def get(self, url: str) -> _FakeResponse:
        self.calls.append(url)
        return _FakeResponse(url, self.html)

    def close(self) -> None:
        pass


@pytest.fixture
def scraper(tmp_path: Path, monkeypatch) -> fetch.Scraper:
    monkeypatch.setattr(auth, "cookies", lambda _: {"T": "x"})
    monkeypatch.setattr(fetch, "cookies", lambda _: {"T": "x"})
    made = fetch.Scraper(cache_dir=tmp_path / "pages", delay=0.0)
    made._client = _FakeClient()
    return made


def test_first_fetch_writes_the_cache(scraper: fetch.Scraper) -> None:
    page = scraper.get("https://example.test/a", key="2023/w01/t01")
    assert not page.from_cache
    assert page.path.exists()
    assert page.path.read_text() == "<html><table>ok</table></html>"


def test_second_fetch_reads_the_cache(scraper: fetch.Scraper) -> None:
    """Selectors will be wrong on the first try; re-parsing must not re-download."""
    scraper.get("https://example.test/a", key="2023/w01/t01")
    again = scraper.get("https://example.test/a", key="2023/w01/t01")
    assert again.from_cache
    assert len(scraper._client.calls) == 1


def test_refresh_forces_a_refetch(scraper: fetch.Scraper) -> None:
    scraper.get("https://example.test/a", key="k")
    scraper.get("https://example.test/a", key="k", refresh=True)
    assert len(scraper._client.calls) == 2


def test_cache_keys_describe_the_page_not_the_url(scraper: fetch.Scraper) -> None:
    """So a Yahoo URL change doesn't invalidate everything already downloaded."""
    page = scraper.get("https://example.test/whatever", key="2023/w05/t04")
    assert page.path.name == "t04.html"
    assert page.path.parent.name == "w05"


def test_season_pages_fetches_each_matchup_once(scraper: fetch.Scraper) -> None:
    """Each matchup page shows both lineups, so only odd team ids are needed."""
    pages = list(fetch.season_pages(scraper, LEAGUE, 2023, teams=10, weeks=range(1, 3)))
    assert len(pages) == 2 * 5  # two weeks, five matchups apiece
    assert all("/2023/f1/" in url for url in scraper._client.calls)


# --------------------------------------------------------------- session


def test_missing_session_is_explained(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="yahoo-auth"):
        auth.cookies(tmp_path / "nope.json")


def test_only_yahoo_cookies_are_kept(tmp_path: Path) -> None:
    """A browser profile carries cookies for everywhere; only Yahoo's belong here."""
    state = tmp_path / "session.json"
    state.write_text(
        '{"cookies": ['
        '{"name": "T", "value": "yes", "domain": ".yahoo.com"},'
        '{"name": "SSL", "value": "yes", "domain": "fantasysports.yahoo.com"},'
        '{"name": "tracker", "value": "no", "domain": ".doubleclick.net"}'
        "]}"
    )
    assert auth.cookies(state) == {"T": "yes", "SSL": "yes"}
