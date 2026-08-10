"""Getting a Yahoo session, once, by hand.

Yahoo's login is defended by two-factor prompts, device checks and bot detection, and
automating it is a bad trade: it's the one step that genuinely needs a human, and it
only has to happen every few weeks. So a real browser opens, you log in, and the
cookies are saved.

Everything afterwards is plain HTTP with those cookies. League pages themselves carry
no such defences -- they're server-rendered and answer a normal GET.
"""

import json
from pathlib import Path

DEFAULT_STATE = Path("data/yahoo/session.json")
LEAGUE_HOME = "https://football.fantasysports.yahoo.com/"

# Kept identical to the browser the session was created in. A mismatched user agent is
# the most common reason a set of Yahoo cookies stops being accepted.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


def capture(state_path: Path = DEFAULT_STATE, timeout_ms: int = 300_000) -> Path:
    """Open a browser, wait for a manual login, and save the session.

    Returns the path written. Requires the `scrape` extra.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "Playwright is needed for the one-time login. Install it with:\n"
            "  uv sync --extra scrape && uv run playwright install chromium"
        ) from exc

    state_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=False)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.goto(LEAGUE_HOME)

        print("\nA browser window has opened.")
        print("  1. Log in to Yahoo.")
        print("  2. Navigate to your league so the session is fully established.")
        print("  3. Come back here and press Enter.\n")
        input("Press Enter once you're looking at your league... ")

        context.storage_state(path=str(state_path))
        browser.close()

    return state_path


def cookies(state_path: Path = DEFAULT_STATE) -> dict[str, str]:
    """The Yahoo cookies from a saved session, ready for an HTTP client."""
    if not state_path.exists():
        raise FileNotFoundError(
            f"No saved session at {state_path}. Run `fantasy-tool yahoo-auth` first."
        )
    state = json.loads(state_path.read_text())
    return {
        cookie["name"]: cookie["value"]
        for cookie in state.get("cookies", [])
        if "yahoo.com" in cookie.get("domain", "")
    }
