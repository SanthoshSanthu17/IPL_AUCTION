"""Collect official IPL portrait URLs for players that have none yet.

The IPL site fills its squad and auction lists in the browser, so this tool
drives a real Chromium window (Playwright), reads every player portrait it
finds, and matches them to ``data/players.csv`` by name. It only fills players
whose ``image_url`` is blank and never replaces an existing portrait. Players it
cannot match uniquely are listed and keep the initials avatar.

One-time setup (inside the project's virtual environment):

    pip install playwright

The tool uses the Google Chrome or Microsoft Edge already installed on the
computer, so no browser download is needed. Use --browser msedge or
--browser chrome to choose one.

Run from the project folder:

    python -m ipl_auction.players.fetch_portraits --apply
    python -m ipl_auction.players.fetch_portraits --apply --show --debug
"""

import argparse
import csv
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from ipl_auction.paths import DATA_DIR
from ipl_auction.players.expand_pool import (
    PLAYER_FILE,
    PORTRAIT_FILE,
    _write_rows,
    apply_portraits,
    load_portrait_overrides,
)
from ipl_auction.players.update_stats import normalize_player_name


TEAM_SLUGS = (
    "chennai-super-kings",
    "delhi-capitals",
    "gujarat-titans",
    "kolkata-knight-riders",
    "lucknow-super-giants",
    "mumbai-indians",
    "punjab-kings",
    "rajasthan-royals",
    "royal-challengers-bengaluru",
    "sunrisers-hyderabad",
)
PAGES = tuple(f"https://www.iplt20.com/teams/{slug}/squad" for slug in TEAM_SLUGS) + (
    "https://www.iplt20.com/auction/2027/to-be-auctioned",
)
DEBUG_FILE = DATA_DIR / "portrait_debug.json"
PROFILE_PATH = re.compile(r"/players/[0-9a-f]{8}-[0-9a-f-]{27}/[^/?#]+")
IMAGE_SCRIPT = """() => [...document.images].map(image => ({
    alt: (image.alt || '').trim(),
    src: image.currentSrc || image.src || '',
    href: (image.closest('a') || {}).href || ''
}))"""


def canonical_image_url(src: str) -> str | None:
    """Rebuild a player portrait URL in the format already used in players.csv."""
    parsed = urlparse(src)
    query = parse_qs(parsed.query)
    if parsed.path.endswith("/_next/image"):
        inner = urlparse(query.get("url", [""])[0])
        if inner.path != "/api/team-assets":
            return None
        query = parse_qs(inner.query)
    elif parsed.path != "/api/team-assets":
        return None
    pathname = query.get("pathname", [""])[0]
    if not re.fullmatch(r"player/\d+/\d+/\d+\.(png|jpg|jpeg|webp)", pathname):
        return None
    inner_url = (
        "/api/team-assets?pathname="
        + quote(pathname, safe="")
        + "&brand=ipl&fallback=fallback-player-men"
    )
    return "https://www.iplt20.com/_next/image?url=" + quote(inner_url, safe="") + "&w=1080&q=75"


def add_found(found: dict[str, dict[str, str]], items: list[dict[str, str]]) -> None:
    for item in items:
        name = item.get("alt", "").strip()
        image_url = canonical_image_url(item.get("src", ""))
        if not name or not image_url:
            continue
        href = item.get("href", "")
        profile = PROFILE_PATH.search(urlparse(href).path)
        found.setdefault(
            normalize_player_name(name),
            {
                "name": name,
                "image_url": image_url,
                "portrait_profile_url": f"https://www.iplt20.com{profile.group(0)}" if profile else "",
            },
        )


def match_names(
    needed: list[str], found: dict[str, dict[str, str]]
) -> tuple[dict[str, dict[str, str]], list[tuple[str, str]], list[str]]:
    """Return (matches by player name, heuristic pairs to review, unmatched names)."""
    matches: dict[str, dict[str, str]] = {}
    heuristic: list[tuple[str, str]] = []
    unmatched: list[str] = []
    for name in needed:
        key = normalize_player_name(name)
        if key in found:
            matches[name] = found[key]
            continue
        tokens = key.split()
        candidates = (
            [
                entry
                for found_key, entry in found.items()
                if found_key.split()[-1] == tokens[-1] and found_key[0] == key[0]
            ]
            if len(tokens) >= 2
            else []
        )
        if len(candidates) == 1:
            matches[name] = candidates[0]
            heuristic.append((name, candidates[0]["name"]))
        else:
            unmatched.append(name)
    return matches, heuristic, unmatched


def _collect_with_scrolling(page, found: dict[str, dict[str, str]]) -> None:
    last_count, stable_rounds = -1, 0
    deadline = time.monotonic() + 75
    for _ in range(80):
        if time.monotonic() > deadline:
            break
        add_found(found, page.evaluate(IMAGE_SCRIPT))
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(600)
        for label in ("load more", "show more", "next"):
            button = page.get_by_role("button", name=re.compile(label, re.IGNORECASE))
            try:
                if button.count() and button.first.is_enabled():
                    button.first.click(timeout=3000)
                    page.wait_for_timeout(900)
                    break
            except Exception:
                pass
        stable_rounds = stable_rounds + 1 if len(found) == last_count else 0
        last_count = len(found)
        if stable_rounds >= 4:
            break
    add_found(found, page.evaluate(IMAGE_SCRIPT))


def _launch_browser(playwright, show_browser: bool, channel: str | None):
    """Start installed Chrome or Edge (or Playwright's own Chromium as a last resort)."""
    last_error: Exception | None = None
    for candidate in ([channel] if channel else ["chrome", "msedge", None]):
        options = {"headless": not show_browser}
        if candidate:
            options["channel"] = candidate
        try:
            return playwright.chromium.launch(**options)
        except Exception as error:
            last_error = error
    raise SystemExit(
        "Could not start Google Chrome or Microsoft Edge. Install one of them, "
        f"or pick one with --browser chrome / --browser msedge.\nLast error: {last_error}"
    )


def harvest(
    pages: tuple[str, ...], show_browser: bool, channel: str | None = None
) -> dict[str, dict[str, str]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SystemExit("Playwright is not installed. Run:  pip install playwright") from error

    found: dict[str, dict[str, str]] = {}
    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, show_browser, channel)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        try:
            for url in pages:
                before = len(found)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3500)
                    _collect_with_scrolling(page, found)
                except Exception as error:
                    print(f"  skipped {url}: {type(error).__name__}")
                    continue
                print(f"  {url}: {len(found) - before} new portraits", flush=True)
        except KeyboardInterrupt:
            print("\nStopped early; using the portraits found so far.")
        browser.close()
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="Find IPL portraits for players without one.")
    parser.add_argument("--apply", action="store_true",
                        help="Write the portraits into data/players.csv (otherwise only portrait_overrides.csv).")
    parser.add_argument("--show", action="store_true", help="Show the browser window while it works.")
    parser.add_argument("--browser", choices=("chrome", "msedge"),
                        help="Installed browser to use (default: try Chrome, then Edge).")
    parser.add_argument("--debug", action="store_true",
                        help="Save everything found to data/portrait_debug.json.")
    args = parser.parse_args()

    with PLAYER_FILE.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    needed = [row["name"] for row in rows if not row.get("image_url")]
    print(f"{len(needed)} players have no portrait yet. Opening IPL pages...")

    found = harvest(PAGES, args.show, args.browser)
    if args.debug:
        DEBUG_FILE.write_text(json.dumps(list(found.values()), indent=2), encoding="utf-8")
    matches, heuristic, unmatched = match_names(needed, found)

    overrides = load_portrait_overrides()
    merged = {
        normalize_player_name(name): {
            "name": name,
            "image_url": entry["image_url"],
            "portrait_profile_url": entry["portrait_profile_url"],
        }
        for name, entry in matches.items()
    }
    for key, entry in overrides.items():
        merged.setdefault(key, {"name": key, **entry})
    with PORTRAIT_FILE.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file, fieldnames=["name", "image_url", "portrait_profile_url"], quoting=csv.QUOTE_ALL
        )
        writer.writeheader()
        writer.writerows(merged.values())

    applied = 0
    if args.apply:
        applied = apply_portraits(rows, load_portrait_overrides())
        _write_rows(rows, fieldnames, PLAYER_FILE)

    print(f"\nFound portraits on the IPL site: {len(found)}")
    print(f"Matched {len(matches)} of {len(needed)} players; "
          f"{applied} written to players.csv." if args.apply
          else f"Matched {len(matches)} of {len(needed)} players (saved to portrait_overrides.csv).")
    if heuristic:
        print("\nReview these automatic name matches:")
        for name, source in heuristic:
            print(f"  {name}  ->  {source}")
    if unmatched:
        print("\nNo portrait found (these keep the initials avatar):")
        for name in unmatched:
            print(f"  {name}")


if __name__ == "__main__":
    main()