"""Walking a league's pages and caching every one.

Yahoo's fantasy pages are server-rendered: a plain authenticated GET returns fully
populated markup, so no browser is needed past the initial login.

Two rules shape this module. Every response is written to disk before anything looks
at it, because the selectors will be wrong on the first attempt and re-parsing must
never mean re-fetching. And a redirect to the login page is treated as a hard failure
rather than parsed as if it were data -- a silently-parsed login page would produce an
empty league that looks like a real one.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from .auth import DEFAULT_STATE, USER_AGENT, cookies

BASE = "https://football.fantasysports.yahoo.com"
DEFAULT_CACHE = Path("data/yahoo/pages")

# Yahoo publishes no rate limit for the web tier. A full league history is a few
# thousand pages; at this pace that's about an hour and invisible to anyone.
DELAY_SECONDS = 1.5


class SessionExpired(RuntimeError):
    """Yahoo bounced us to the login page. Re-run `fantasy-tool yahoo-auth`."""


@dataclass(frozen=True, slots=True)
class Page:
    """One fetched page, with where it came from and where it's cached."""

    url: str
    path: Path
    html: str
    from_cache: bool


def league_url(league_id: str, season: int | None = None, path: str = "", game: str = "f1") -> str:
    """Build a league URL.

    Past seasons live under a year-prefixed path and, importantly, under a *different*
    league id -- Yahoo issues a new one each time a league renews. The current season
    has no year prefix.
    """
    prefix = f"/{season}" if season else ""
    suffix = f"/{path.lstrip('/')}" if path else ""
    return f"{BASE}{prefix}/{game}/{league_id}{suffix}"


def matchup_url(
    league_id: str, team_id: int, week: int, season: int | None = None, game: str = "f1"
) -> str:
    """A team's matchup page for one week.

    Note this page is *personal*: Yahoo ignores the team id and renders the logged-in
    user's own matchup whatever you ask for. It is useful for one thing only -- it
    prints Yahoo's own scores, which is how the parser is validated -- and a whole
    league's lineups come from `season.starters_url` instead.
    """
    return league_url(league_id, season, f"{team_id}/matchup?week={week}", game)


class Scraper:
    """Fetches Yahoo pages with a saved session, caching everything."""

    def __init__(
        self,
        state_path: Path = DEFAULT_STATE,
        cache_dir: Path = DEFAULT_CACHE,
        delay: float = DELAY_SECONDS,
    ) -> None:
        import httpx

        self.cache_dir = cache_dir
        self.delay = delay
        self._last_request = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            cookies=cookies(state_path),
            follow_redirects=True,
            timeout=20.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.html"

    def get(self, url: str, key: str, refresh: bool = False) -> Page:
        """Fetch a page, or read it back from the cache.

        `key` is the cache filename, so it should describe the page rather than the
        URL: `2023/w05/t04` survives Yahoo changing its URL scheme.
        """
        path = self.cache_path(key)
        if path.exists() and not refresh:
            return Page(url=url, path=path, html=path.read_text(), from_cache=True)

        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

        response = self._client.get(url)
        check_response(str(response.url), response.text)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(response.text)
        return Page(url=url, path=path, html=response.text, from_cache=False)


def check_response(final_url: str, html: str) -> None:
    """Refuse anything that isn't the page we asked for.

    Yahoo answers an unauthenticated request with a redirect to login rather than an
    error, so without this the parser would happily read a login form and report a
    league where every lineup was empty.
    """
    if "login.yahoo.com" in final_url:
        raise SessionExpired(
            "Yahoo redirected to the login page -- the session has expired. "
            "Re-run `fantasy-tool yahoo-auth`."
        )
    if "The document you requested was not found" in html:
        raise FileNotFoundError(f"Yahoo says this page doesn't exist: {final_url}")
