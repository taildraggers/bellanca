"""Scraper for Bellanca taildragger listings on barnstormers.com.

Bellanca originally produced the Citabria/Decathlon/Scout family (taken
over from Champion Aircraft Corporation in 1970) before selling the line
to American Champion Aircraft in the early 1990s - see the companion
[American Champion](https://github.com/taildraggers/american-champion)
repo for the same model family under its current manufacturer. This repo
targets the Bellanca-branded (pre-American-Champion) aircraft.

Barnstormers' single-manufacturer category pages (the same pattern seen in
the companion Aviat, CubCrafters, de Havilland, Maule, Van's RV, RANS,
Luscombe, Just Aircraft, and Kitfox repos) can mix in off-brand or
off-topic listings with no distinguishing HTML markup from the genuine
ones. So results are filtered by title against a small allowlist of
Bellanca product names before being published.

On top of that brand allowlist, only whole-aircraft-for-sale listings are
kept: each ad's title must match a recognized model code or name, and
titles that look like parts/accessories/services/raffles are dropped.
Surviving titles are rewritten to a canonical "YEAR BELLANCA MODEL" form
when the ad states a model year, or just "BELLANCA MODEL" when it
doesn't.

Model codes (7ECA, 7GCAA, 7GCBC, 7KCAB, 8KCAB, 8GCBC, 7EC) and the coined
names Citabria/Decathlon/Super Decathlon/Xtreme Decathlon are trusted
standalone - none of these collide with ordinary English usage. "Scout"
and "Explorer" are plain English words with real collision risk (a Ford
Explorer, "scout" as a common noun/verb), so - the lesson learned the
hard way in the companion Piper repo, where a bare "Cub" mislabeled
non-Piper homebuilts as genuine Pipers - each of those requires the title
to also say "Bellanca" explicitly.

taildraggers.com is taildragger-only. The Citabria/Decathlon/Scout family
has no known factory tricycle-gear variant (unlike the unrelated Bellanca
Viking/Super Viking - a separate, retractable-tricycle-gear model line
that Barnstormers' own "Taildragger" category should already exclude), so
there's no categorical model exclusion here. As a general safety net, the
same policy applied in the companion RANS, Luscombe, Just Aircraft, and
Kitfox repos still holds: any individual ad of any model whose own text
explicitly says tricycle/trike/nosewheel gear is dropped.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from .common import (
    Listing,
    extract_date,
    extract_location,
    extract_price,
    fetch,
    format_aircraft_title,
)

SITE_NAME = "Barnstormers.com"
BASE = "https://www.barnstormers.com"
MAKE = "Bellanca"

# Category pages for Bellanca taildragger listings on Barnstormers. There
# are two: one under the "Bellanca" brand name and a separate one under
# the "Citabria" model name (Barnstormers splits some listings across
# both rather than filing everything under the manufacturer).
CATEGORY_URLS = [
    f"{BASE}/category-22245-Taildragger--Bellanca.html",
    f"{BASE}/category-22289-Taildragger--Citabria.html",
]

MAX_PAGES = 10
LISTING_LINK_RE = re.compile(r"^/classified-(\d+)-(.+)\.html$")
GENERIC_SITE_TITLE_SNIPPET = "barnstormers.com find aircraft"


def _compact(text: str) -> str:
    return re.sub(r"[\s-]", "", text.lower())


# High-confidence model codes/names, trusted standalone since none collide
# with ordinary English usage - see module docstring.
_MODEL_CODE_RE = re.compile(
    r"\b(7gcaa|7gcbc|7eca|7kcab|8kcab|8gcbc|7ec)\b", re.IGNORECASE
)
_MODEL_NAME_RULES = [
    (re.compile(r"\bxtreme\s*decathlon\b", re.IGNORECASE), "Xtreme Decathlon"),
    (re.compile(r"\bsuper\s*decathlon\b", re.IGNORECASE), "Super Decathlon"),
    (re.compile(r"\bdecathlon\b", re.IGNORECASE), "Decathlon"),
    (re.compile(r"\bcitabria\b", re.IGNORECASE), "Citabria"),
]

# Generic-English-word model names, only trusted when the title also says
# "Bellanca" explicitly - see module docstring.
_MARKETING_NAME_RULES = [
    (re.compile(r"\bhigh\s*country\s*explorer\b", re.IGNORECASE), "High Country Explorer"),
    (re.compile(r"\bexplorer\b", re.IGNORECASE), "Explorer"),
    (re.compile(r"\bscout\b", re.IGNORECASE), "Scout"),
]
_BRAND_RE = re.compile(r"\bbellanca\b", re.IGNORECASE)

# Only ads whose title matches one of these (case/hyphen/space-insensitive,
# compared against a fully compacted - no spaces or hyphens - form of the
# title) are kept, since the category page itself isn't reliably
# Bellanca-only. "Scout"/"Explorer" are deliberately excluded from this
# coarse gate too - too generic to trust even here.
TARGET_MODEL_PHRASES = [
    "bellanca", "citabria", "decathlon",
    "7eca", "7gcaa", "7gcbc", "7kcab", "8kcab", "8gcbc", "7ec",
]


def _matches_target_models(title: str) -> bool:
    compact = _compact(title)
    return any(phrase in compact for phrase in TARGET_MODEL_PHRASES)


# Ads whose title or body text explicitly calls out tricycle/nosewheel gear
# are dropped, regardless of which model they are - see module docstring.
_NON_TAILWHEEL_KEYWORDS = (
    "tricycle gear",
    "tricycle landing gear",
    "trike gear",
    "tri-gear",
    "tri gear",
    "nosewheel",
    "nose wheel",
    "nose-wheel",
)


def _is_non_tailwheel(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in _NON_TAILWHEEL_KEYWORDS)


def _extract_model(title: str) -> tuple[str, str] | None:
    for pattern, canonical in _MODEL_NAME_RULES:
        if pattern.search(title):
            return MAKE, canonical

    match = _MODEL_CODE_RE.search(title)
    if match:
        return MAKE, match.group(1).upper()

    if _BRAND_RE.search(title):
        for pattern, canonical in _MARKETING_NAME_RULES:
            if pattern.search(title):
                return MAKE, canonical
    return None


def _title_from_url(url: str) -> str:
    """Listing pages share a generic <title>/<h1>, but the URL slug is the ad's own title."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    match = LISTING_LINK_RE.match("/" + slug)
    if not match:
        return unquote(slug)
    return unquote(match.group(2)).replace("-", " ").strip()


def _find_listing_links(html: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if LISTING_LINK_RE.match(href):
            links.add(urljoin(BASE, href))
    return links


def _find_next_page_url(html: str, current_url: str) -> str | None:
    """Find a "next page" link on a category listing page, if any."""
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        rel = a.get("rel") or []
        if text in ("next", "next »", "»", "next page", ">") or "next" in rel:
            candidate = urljoin(current_url, a["href"])
            if candidate != current_url:
                return candidate
    return None


def _debug_dump_hrefs(html: str, limit: int = 25) -> None:
    soup = BeautifulSoup(html, "lxml")
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    interesting = [h for h in hrefs if "classified" in h.lower() or "bellanca" in h.lower()]
    sample = interesting[:limit] or hrefs[:limit]
    print(f"  [debug] {len(hrefs)} total <a href> on page; sample: {sample}")


def _parse_detail_page(url: str, html: str) -> Listing | None:
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None
    if title:
        title = re.sub(r"\s*[\|\-]\s*Barnstormers.*$", "", title, flags=re.IGNORECASE).strip()
    if not title or GENERIC_SITE_TITLE_SNIPPET in title.lower():
        title = _title_from_url(url)
    if not title:
        return None

    if not _matches_target_models(title):
        return None

    text = soup.get_text(" ", strip=True)

    if _is_non_tailwheel(title) or _is_non_tailwheel(text):
        return None

    formatted_title = format_aircraft_title(title, text, _extract_model)
    if not formatted_title:
        return None
    title = formatted_title

    price = extract_price(text)
    location = extract_location(text)
    date_posted = extract_date(text)

    return Listing(
        title=title,
        price=price,
        location=location,
        date_posted=date_posted,
        site=SITE_NAME,
        url=url,
    )


def scrape() -> list[Listing]:
    print(f"[{SITE_NAME}] starting scrape")
    all_links: set[str] = set()

    for category_url in CATEGORY_URLS:
        seen_this_category: set[str] = set()
        url = category_url
        for page in range(1, MAX_PAGES + 1):
            html = fetch(url)
            if not html:
                break
            links = _find_listing_links(html)
            new_links = links - seen_this_category
            print(f"  [{category_url}] page {page}: {len(links)} links ({len(new_links)} new)")
            if page == 1 and not links:
                _debug_dump_hrefs(html)
            seen_this_category |= links
            next_url = _find_next_page_url(html, url)
            if not next_url or not new_links:
                break
            url = next_url
        all_links |= seen_this_category

    print(f"[{SITE_NAME}] {len(all_links)} unique listing URLs found")

    candidate_links = {url for url in all_links if _matches_target_models(_title_from_url(url))}
    print(f"[{SITE_NAME}] {len(candidate_links)} match Bellanca product names")

    listings: list[Listing] = []
    for url in sorted(candidate_links):
        html = fetch(url)
        if not html:
            continue
        listing = _parse_detail_page(url, html)
        if listing:
            listings.append(listing)

    print(f"[{SITE_NAME}] parsed {len(listings)} listings")
    return listings
