from __future__ import annotations

import hashlib
import io
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar
from urllib.parse import quote, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from models import (
    FinanceSummary,
    GovernmentRecord,
    Headline,
    Hearing,
    LegislativeItem,
    LegislatorSocialAccount,
    Milestone,
    PoliticalEvent,
    SocialPost,
    SourceResult,
)


CENTRAL = ZoneInfo("America/Chicago")
NOW = lambda: datetime.now(CENTRAL)
T = TypeVar("T")
CURRENT_YEAR = NOW().year

LRL_X_DIRECTORY = "https://lrl.texas.gov/legeleaders/members/twitterDirectory.cfm"
LRL_X_LIST = "https://twitter.com/TexasLRL/lists/TxLegislators"
LRL_X_LIST_ID = "31904710"
# X's public syndication endpoint — the same JSON the embed widgets read. No token required.
X_SYNDICATION_TIMELINE = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}"
# X allows 30 anonymous syndication requests per window per IP and is also
# burst-sensitive: parallel request waves draw 429s well under the quota, so the
# public feed fetches sequentially with a politeness delay between requests.
PUBLIC_FEED_MAX_ACCOUNTS = 6
PUBLIC_INDEX_MAX_ACCOUNTS = 18
PUBLIC_FEED_CONCURRENCY = 1
# Fallback handles used only when the LRL directory itself is unavailable.
DEFAULT_X_HANDLES = (
    "SenBryanHughes",
    "leachfortexas",
    "joanhuffman",
    "DonnaCampbellTX",
    "Burrows4TX",
)
TEC_FINANCE_XLSX = (
    "https://www.ethics.state.tx.us/data/search/cf/2026/2026_PACs_By_Total_Contribs.xlsx"
)
TEC_FINANCE_HOME = "https://www.ethics.state.tx.us/search/cf/"
TRIBUNE_FEED = "https://feeds.texastribune.org/feeds/main/"
DIRECT_NEWS_FEEDS = {
    "Texas Scorecard": "https://texasscorecard.com/feed/",
    "Current Revolt": "https://www.currentrevolt.com/feed",
}
THE_TEXAN_HOME = "https://thetexan.news/"
TEXAS_BULLPEN_DAILY = "https://texasbullpen.com/daily-bull/"
def _google_news_url(query: str) -> str:
    return "https://news.google.com/rss/search?" + urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    )


GOOGLE_NEWS_FEEDS = {
    "Texas politics news": _google_news_url(
        'Texas (politics OR policy OR election OR government) when:7d'
    ),
    "Texas Legislature news": _google_news_url(
        '"Texas Legislature" OR txlege OR ("Texas Senate" bill) OR ("Texas House" bill) when:7d'
    ),
    "Texas Republican news": _google_news_url(
        '"Texas GOP" OR ("Texas Republican" election) OR ("Texas Republican" legislature) when:7d'
    ),
}

TLO_FEEDS = {
    "House bills filed": "https://capitol.texas.gov/MyTLO/RSS/RSS.aspx?Type=todaysfiledhouse",
    "Senate bills filed": "https://capitol.texas.gov/MyTLO/RSS/RSS.aspx?Type=todaysfiledsenate",
    "House calendars": "https://capitol.texas.gov/MyTLO/RSS/RSS.aspx?Type=upcomingcalendarshouse",
    "Senate calendars": "https://capitol.texas.gov/MyTLO/RSS/RSS.aspx?Type=upcomingcalendarssenate",
    "Fiscal notes": "https://capitol.texas.gov/MyTLO/RSS/RSS.aspx?Type=todaysfiscalnotes",
    "Bill analyses": "https://capitol.texas.gov/MyTLO/RSS/RSS.aspx?Type=todaysbillanalyses",
    "Passed bills": "https://capitol.texas.gov/MyTLO/RSS/RSS.aspx?Type=todaysbillspassed",
    "Bill text": "https://capitol.texas.gov/MyTLO/RSS/RSS.aspx?Type=todaysbilltext",
}
TLO_HEARING_FEEDS = {
    "House": "https://capitol.texas.gov/MyTLO/RSS/RSS.aspx?Type=upcomingmeetingshouse",
    "Senate": "https://capitol.texas.gov/MyTLO/RSS/RSS.aspx?Type=upcomingmeetingssenate",
}

TLO_VOTE_SOURCES = {
    "House votes by date": "https://capitol.texas.gov/Reports/GeneralVotesByDateHouse.aspx",
    "Senate votes by date": "https://capitol.texas.gov/Reports/GeneralVotesByDateSenate.aspx",
    "Bill amendments and companions": "https://capitol.texas.gov/MnuLegislation.aspx",
}

OPEN_MEETINGS_BACKUP = (
    "https://texinfo.library.unt.edu/texasregister/openmeetings/OpenMeetings.html"
)
OPEN_MEETINGS_PRIMARY = (
    "https://texas-sos.appianportalsgov.com/rules-and-meetings"
    "?interface=SEARCH_OPEN_MEETINGS"
)
TEXAS_REGISTER_CURRENT = "https://www.sos.state.tx.us/texreg/sos/"
TEC_DIRECT_EXPENDITURES = (
    f"https://www.ethics.state.tx.us/search/cf/{CURRENT_YEAR}/"
    f"DirectExpenditures{CURRENT_YEAR}.html"
)
TEC_LOBBY_ROSTER = (
    f"https://www.ethics.state.tx.us/data/search/lobby/{CURRENT_YEAR}/"
    f"{CURRENT_YEAR}LobbyGroupByClient.xlsx"
)
TEC_LOBBY_HOME = "https://www.ethics.state.tx.us/search/lobby/"
TEC_LOBBY_ACTIVITY = (
    f"https://www.ethics.state.tx.us/search/lobby/{CURRENT_YEAR}/"
    f"LAReportsByYear{CURRENT_YEAR}.html"
)
GOVERNOR_NEWS = "https://gov.texas.gov/news"
SUPREME_COURT_ORDERS = (
    f"https://www.txcourts.gov/supreme/orders-opinions/{CURRENT_YEAR}/"
)
ELECTION_DATA_HOME = "https://www.sos.state.tx.us/elections/historical/index.shtml"
COMPTROLLER_CONTRACTS = (
    "https://comptroller.texas.gov/transparency/open-data/cpa-contracts.php"
)
LBB_HOME = "https://www.lbb.texas.gov/"

EVENT_SOURCES = (
    {
        "name": "Tarrant County GOP",
        "region": "Dallas–Fort Worth",
        "kind": "tribe",
        "url": "https://tarrantgop.org/wp-json/tribe/events/v1/events",
        "page": "https://tarrantgop.org/events/",
    },
    {
        "name": "Dallas County GOP",
        "region": "Dallas–Fort Worth",
        "kind": "tribe",
        "url": "https://dallasgop.org/wp-json/tribe/events/v1/events",
        "page": "https://dallasgop.org/events/month/",
    },
    {
        "name": "Harris County GOP",
        "region": "Houston",
        "kind": "html",
        "url": "https://www.harriscountygop.com/events/",
        "page": "https://www.harriscountygop.com/events/",
    },
    {
        "name": "Travis County GOP",
        "region": "Austin",
        "kind": "html",
        "url": "https://traviscountygop.org/events",
        "page": "https://traviscountygop.org/events",
    },
    {
        "name": "Williamson County GOP",
        "region": "Austin",
        "kind": "tribe",
        "url": "https://wilcogop.org/wp-json/tribe/events/v1/events",
        "page": "https://wilcogop.org/calendar/",
    },
    {
        "name": "Bexar County GOP",
        "region": "San Antonio",
        "kind": "html",
        "url": "https://bexargop.org/county-calendar/",
        "page": "https://bexargop.org/county-calendar/",
    },
    {
        "name": "Republican Club of Bexar County",
        "region": "San Antonio",
        "kind": "html",
        "url": "https://bexargopclub.com/",
        "page": "https://bexargopclub.com/",
    },
    {
        "name": "Republican Party of Texas",
        "region": "Statewide",
        "kind": "tribe",
        "url": "https://texasgop.org/wp-json/tribe/events/v1/events",
        "page": "https://texasgop.org/events/",
    },
    {
        "name": "Texas Federation of Republican Women",
        "region": "Statewide",
        "kind": "html",
        "url": "https://www.tfrw.org/events/?ical=1",
        "page": "https://www.tfrw.org/events/",
    },
    {
        "name": "Greater Houston Council of Republican Women",
        "region": "Houston",
        "kind": "html",
        "url": "https://www.ghcfrwpac.org/events/",
        "page": "https://www.ghcfrwpac.org/events/",
    },
    {
        "name": "Montgomery County Republican Party",
        "region": "Houston",
        "kind": "ics",
        "url": "https://www.calendarwiz.com/CalendarWiz_iCal.php?crd=mctxgop",
        "page": "https://mctxgop.org/calendar",
    },
)

ISSUE_TERMS = (
    "texas legislature",
    "txlege",
    "republican",
    "election",
    "border",
    "property tax",
    "energy",
    "grid",
    "education",
    "school choice",
    "public safety",
    "campaign",
    "voter",
    "budget",
    "committee",
    "regulation",
    "attorney general",
    "supreme court",
)

TEXAS_ANCHORS = (
    "texas",
    "txlege",
    "abbott",
    "paxton",
    "patrick",
    "ercot",
    "austin",
    "dallas",
    "houston",
    "san antonio",
    "fort worth",
)

POLITICAL_TERMS = (
    "legislature",
    "senate",
    "house",
    "governor",
    "election",
    "campaign",
    "voter",
    "ballot",
    "bill",
    "committee",
    "hearing",
    "agency",
    "regulator",
    "attorney general",
    "supreme court",
    "county judge",
    "gop",
    "republican",
    "democrat",
    "policy",
    "tax",
    "budget",
    "border",
    "grid",
    "education",
    "congress",
)

ACTION_TERMS = (
    "files",
    "filed",
    "passes",
    "passed",
    "vote",
    "hearing",
    "deadline",
    "rule",
    "ruling",
    "orders",
    "audit",
    "investigation",
    "funding",
    "primary",
    "general election",
)

TOPIC_LABELS = {
    "texas legislature": "Legislature",
    "txlege": "Legislature",
    "republican": "GOP",
    "election": "Elections",
    "campaign": "Elections",
    "border": "Border",
    "property tax": "Property tax",
    "energy": "Energy",
    "grid": "Grid",
    "education": "Education",
    "school choice": "School choice",
    "public safety": "Public safety",
}


def extract_topics(text: str, limit: int = 3) -> list[str]:
    """Human-readable issue tags found in a headline or summary, in fixed order."""
    lowered = text.lower()
    labels: list[str] = []
    for term, label in TOPIC_LABELS.items():
        if term in lowered and label not in labels:
            labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def parse_watch_terms(value: str | Iterable[str]) -> tuple[str, ...]:
    """Normalize a user watchlist without changing the analyst's display wording."""
    raw_terms = value.split(",") if isinstance(value, str) else value
    seen: set[str] = set()
    terms: list[str] = []
    for raw in raw_terms:
        term = clean_text(str(raw)).strip()
        key = term.casefold()
        if key and key not in seen:
            seen.add(key)
            terms.append(term)
    return tuple(terms)


def matched_watch_terms(text: str, terms: Iterable[str]) -> tuple[str, ...]:
    """Return each configured term that occurs in normalized source text.

    Phrase matching is intentionally transparent: analysts can see exactly why
    a record entered a watchlist rather than relying on an opaque score.
    """
    haystack = clean_text(text).casefold()
    return tuple(term for term in terms if term.casefold() in haystack)


def headline_relevance(title: str, summary: str = "", publisher: str = "") -> int:
    """Score Texas political stories for an operational intelligence reader."""
    haystack = f"{title} {summary}".lower()
    score = sum(2 for term in ISSUE_TERMS if term in haystack)
    score += min(6, sum(2 for term in TEXAS_ANCHORS if term in haystack))
    score += min(6, sum(2 for term in POLITICAL_TERMS if term in haystack))
    score += min(4, sum(1 for term in ACTION_TERMS if term in haystack))
    if publisher in {"The Texas Tribune", "The Texan", "Texas Scorecard", "Current Revolt", "Texas Bullpen"}:
        score += 2
    return score


def headline_priority(item: Headline, now: datetime | None = None) -> float:
    """Blend operational relevance with a three-day recency decay."""
    reference = now or NOW()
    if not item.published_at:
        freshness = 0.0
    else:
        age_hours = max(
            0.0,
            (reference - item.published_at.astimezone(CENTRAL)).total_seconds() / 3600,
        )
        freshness = max(0.0, 12.0 - age_hours / 6.0)
    return item.relevance + freshness


def is_texas_political_story(title: str, summary: str = "") -> bool:
    """Keep broad aggregator feeds focused on Texas government and politics."""
    haystack = f"{title} {summary}".lower()
    has_texas_anchor = any(term in haystack for term in TEXAS_ANCHORS)
    has_political_anchor = any(term in haystack for term in POLITICAL_TERMS)
    return has_texas_anchor and has_political_anchor


class ResilientClient:
    """HTTP client with retries and an in-process last-good response store."""

    def __init__(
        self,
        timeout: float = 8.0,
        status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504),
    ) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.35,
            status_forcelist=status_forcelist,
            allowed_methods=frozenset({"GET"}),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36 "
                    "LoneStarLedger/2.0"
                ),
                "Accept": "application/json, application/xml, text/html, */*",
            }
        )
        self._last_good: dict[str, bytes] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _validate(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Only absolute HTTPS source URLs are allowed")

    def get(
        self, url: str, headers: dict[str, str] | None = None
    ) -> tuple[bytes, bool, int, str]:
        self._validate(url)
        started = time.perf_counter()
        key = hashlib.sha256(url.encode()).hexdigest()
        try:
            response = self.session.get(url, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            body = response.content
            with self._lock:
                self._last_good[key] = body
            return body, False, int((time.perf_counter() - started) * 1000), ""
        except requests.RequestException:
            with self._lock:
                stale = self._last_good.get(key)
            latency = int((time.perf_counter() - started) * 1000)
            if stale is not None:
                return stale, True, latency, "The source is unavailable; showing the last successful response."
            return b"", False, latency, "The source could not be reached."


CLIENT = ResilientClient()


class SyndicationClient(ResilientClient):
    """Anonymous X syndication client that respects the 30-requests-per-window quota.

    A 429 closes a gate until X's advertised reset time; while closed, no network
    calls are made and the last successful body is served as stale. Last-good
    bodies are also written to disk so they survive restarts and cache clears.
    """

    def __init__(self, cache_dir: str | None = None) -> None:
        # Never retry 429s — retrying only deepens the anonymous rate limit.
        super().__init__(timeout=6.0, status_forcelist=(500, 502, 503, 504))
        self._blocked_until = 0.0
        directory = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir()) / "lone-star-ledger-x"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            self.cache_dir: Path | None = directory
        except OSError:
            self.cache_dir = None

    def blocked_seconds(self) -> int:
        with self._lock:
            return max(0, int(self._blocked_until - time.time()))

    def _disk_read(self, key: str) -> bytes | None:
        if not self.cache_dir:
            return None
        try:
            return (self.cache_dir / f"{key}.bin").read_bytes()
        except OSError:
            return None

    def _disk_write(self, key: str, body: bytes) -> None:
        if not self.cache_dir:
            return
        try:
            (self.cache_dir / f"{key}.bin").write_bytes(body)
        except OSError:
            pass

    def _stale(self, key: str, latency: int, error: str) -> tuple[bytes, bool, int, str]:
        with self._lock:
            body = self._last_good.get(key)
        if body is None:
            body = self._disk_read(key)
        if body is not None:
            return body, True, latency, f"{error} Showing the last successful response."
        return b"", False, latency, error

    def get(
        self, url: str, headers: dict[str, str] | None = None
    ) -> tuple[bytes, bool, int, str]:
        self._validate(url)
        key = hashlib.sha256(url.encode()).hexdigest()
        wait = self.blocked_seconds()
        if wait:
            minutes = max(1, -(-wait // 60))
            return self._stale(
                key, 0, f"X's anonymous quota is used up for about {minutes} more minutes."
            )
        started = time.perf_counter()
        try:
            response = self.session.get(url, headers=headers, timeout=self.timeout)
            latency = int((time.perf_counter() - started) * 1000)
            if response.status_code == 429:
                now = time.time()
                try:
                    reset = float(response.headers.get("x-rate-limit-reset", ""))
                except ValueError:
                    reset = 0.0
                with self._lock:
                    self._blocked_until = min(max(reset, now + 60.0), now + 3600.0)
                return self._stale(key, latency, "X rate-limited this address.")
            response.raise_for_status()
            body = response.content
            with self._lock:
                self._last_good[key] = body
            self._disk_write(key, body)
            # Space out consecutive network hits — X 429s bursts under the quota.
            time.sleep(0.4)
            return body, False, latency, ""
        except requests.RequestException:
            latency = int((time.perf_counter() - started) * 1000)
            return self._stale(key, latency, "The source could not be reached.")


SYNDICATION_CLIENT = SyndicationClient()


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    if "<" not in value and ">" not in value:
        return re.sub(r"\s+", " ", unescape(value)).strip()
    return re.sub(
        r"\s+", " ", BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    ).strip()


def safe_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CENTRAL)
    return parsed.astimezone(CENTRAL)


def _feed_entries(xml_bytes: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    output: list[dict[str, Any]] = []
    for node in nodes:
        def text(*names: str) -> str:
            for name in names:
                found = node.find(name)
                if found is not None and found.text:
                    return found.text.strip()
            return ""

        link_node = node.find("link")
        link = text("link")
        if link_node is not None and not link:
            link = link_node.attrib.get("href", "")
        output.append(
            {
                "title": clean_text(text("title", "{http://www.w3.org/2005/Atom}title")),
                "summary": clean_text(
                    text(
                        "description",
                        "{http://www.w3.org/2005/Atom}summary",
                        "{http://www.w3.org/2005/Atom}content",
                    )
                ),
                "link": link,
                "published": safe_datetime(
                    text(
                        "pubDate",
                        "{http://purl.org/dc/elements/1.1/}date",
                        "{http://www.w3.org/2005/Atom}updated",
                    )
                ),
            }
        )
    return output


def _result(
    source_name: str,
    source_url: str,
    items: list[T],
    stale: bool,
    latency: int,
    error: str,
) -> SourceResult[T]:
    # An accessible source with no current records is healthy, not offline. Adapters
    # set an error when the request or parser actually fails.
    freshness = "stale" if stale else ("unavailable" if error and not items else "live")
    return SourceResult(
        source_name=source_name,
        source_url=source_url,
        items=items,
        fetched_at=NOW(),
        freshness=freshness,
        latency_ms=latency,
        error=error if error else ("" if items else "Source responded normally; no current records."),
    )


def fetch_legislative_feed(name: str, url: str) -> SourceResult[LegislativeItem]:
    body, stale, latency, error = CLIENT.get(url)
    items: list[LegislativeItem] = []
    if body:
        try:
            chamber = "House" if "House" in name else "Senate" if "Senate" in name else "Both"
            items = [
                LegislativeItem(
                    title=x["title"],
                    activity_type=name,
                    chamber=chamber,
                    summary=x["summary"],
                    url=x["link"],
                    published_at=x["published"],
                )
                for x in _feed_entries(body)
            ]
        except (ET.ParseError, ValueError):
            error = "The legislative feed returned an unreadable response."
    return _result(name, url, items, stale, latency, error)


def fetch_legislative_activity() -> list[SourceResult[LegislativeItem]]:
    return _parallel(
        [(name, lambda n=name, u=url: fetch_legislative_feed(n, u)) for name, url in TLO_FEEDS.items()]
    )


def fetch_hearing_feed(chamber: str, url: str) -> SourceResult[Hearing]:
    body, stale, latency, error = CLIENT.get(url)
    items: list[Hearing] = []
    if body:
        try:
            for entry in _feed_entries(body):
                combined = f"{entry['title']} {entry['summary']}"
                committee = re.sub(
                    r"\s*-\s*\d{1,2}/\d{1,2}/\d{4}\s*$", "", entry["title"]
                ).strip()
                location_match = re.search(r"(?i)location:\s*([^|;]+)", combined)
                date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", entry["title"])
                time_match = re.search(r"(?i)time:\s*(\d{1,2}:\d{2}\s*[AP]M)", entry["summary"])
                starts_at = entry["published"]
                if date_match and time_match:
                    try:
                        starts_at = datetime.strptime(
                            f"{date_match.group(1)} {time_match.group(1).upper()}",
                            "%m/%d/%Y %I:%M %p",
                        ).replace(tzinfo=CENTRAL)
                    except ValueError:
                        pass
                items.append(
                    Hearing(
                        title=entry["title"],
                        chamber=chamber,
                        committee=committee or entry["title"],
                        location=location_match.group(1).strip() if location_match else "",
                        url=entry["link"],
                        starts_at=starts_at,
                        summary=entry["summary"],
                    )
                )
        except (ET.ParseError, ValueError):
            error = "The hearing feed returned an unreadable response."
    return _result(f"{chamber} hearings", url, items, stale, latency, error)


def fetch_hearings() -> list[SourceResult[Hearing]]:
    return _parallel(
        [
            (chamber, lambda c=chamber, u=url: fetch_hearing_feed(c, u))
            for chamber, url in TLO_HEARING_FEEDS.items()
        ]
    )


def parse_lrl_directory(html: bytes) -> list[LegislatorSocialAccount]:
    soup = BeautifulSoup(html, "html.parser")
    accounts: list[LegislatorSocialAccount] = []
    for anchor in soup.select('a[href*="twitter.com/"], a[href*="x.com/"]'):
        handle = clean_text(anchor.get_text())
        href = anchor.get("href", "")
        if not handle or handle.lower() in {"single feed", "x"}:
            continue
        row = anchor.find_parent("tr")
        if row:
            cells = [clean_text(cell.get_text()) for cell in row.find_all(["td", "th"])]
            if len(cells) >= 3:
                name, chamber = cells[0], cells[1]
            else:
                continue
        else:
            line = clean_text(anchor.parent.get_text(" ", strip=True)) if anchor.parent else ""
            match = re.match(r"(.+?)\s+([HS])\s+" + re.escape(handle) + r"$", line)
            if not match:
                continue
            name, chamber = match.group(1), match.group(2)
        normalized_chamber = {"H": "House", "S": "Senate"}.get(chamber, chamber)
        accounts.append(
            LegislatorSocialAccount(
                name=name,
                chamber=normalized_chamber,
                handle=handle.lstrip("@"),
                profile_url=href.replace("http://", "https://"),
            )
        )
    unique = {item.handle.lower(): item for item in accounts}
    return sorted(unique.values(), key=lambda item: item.name)


def fetch_social_directory() -> SourceResult[LegislatorSocialAccount]:
    body, stale, latency, error = CLIENT.get(LRL_X_DIRECTORY)
    items: list[LegislatorSocialAccount] = []
    if body:
        try:
            items = parse_lrl_directory(body)
        except Exception:
            error = "The LRL directory format could not be read."
    return _result("Legislative Reference Library", LRL_X_DIRECTORY, items, stale, latency, error)


def fetch_social_posts(
    bearer_token: str,
    base_url: str,
    accounts: list[LegislatorSocialAccount],
) -> SourceResult[SocialPost]:
    source_url = base_url.rstrip("/")
    if not bearer_token or not accounts:
        return SourceResult(
            source_name="X API",
            source_url=source_url,
            fetched_at=NOW(),
            freshness="unavailable",
            error="X read access is not configured.",
        )
    selected = accounts[:10]
    headers = {"Authorization": f"Bearer {bearer_token}"}
    lookup = f"{source_url}/2/users/by?{urlencode({'usernames': ','.join(a.handle for a in selected), 'user.fields': 'name,username'})}"
    body, stale, latency, error = CLIENT.get(lookup, headers=headers)
    posts: list[SocialPost] = []
    if body:
        try:
            users = requests.models.complexjson.loads(body).get("data", [])
            by_handle = {a.handle.lower(): a for a in selected}
            for user in users:
                query = urlencode(
                    {
                        "max_results": 5,
                        "exclude": "retweets,replies",
                        "tweet.fields": "created_at,public_metrics",
                    }
                )
                tweet_body, tweet_stale, _, tweet_error = CLIENT.get(
                    f"{source_url}/2/users/{user['id']}/tweets?{query}", headers=headers
                )
                stale = stale or tweet_stale
                error = error or tweet_error
                for post in requests.models.complexjson.loads(tweet_body or b"{}").get("data", []):
                    metrics = post.get("public_metrics", {})
                    account = by_handle.get(user["username"].lower())
                    posts.append(
                        SocialPost(
                            legislator_name=account.name if account else user.get("name", ""),
                            handle=user["username"],
                            text=post.get("text", ""),
                            url=f"https://x.com/{user['username']}/status/{post['id']}",
                            created_at=safe_datetime(post.get("created_at")),
                            likes=int(metrics.get("like_count", 0)),
                            reposts=int(metrics.get("retweet_count", 0)),
                        )
                    )
        except (ValueError, KeyError):
            error = "X returned an unreadable or unauthorized response."
    posts.sort(key=lambda item: item.created_at.timestamp() if item.created_at else 0, reverse=True)
    return _result("X API", source_url, posts, stale, latency, error)


def fetch_x_list_posts(bearer_token: str, base_url: str) -> SourceResult[SocialPost]:
    source_url = "https://x.com/i/lists/31904710"
    if not bearer_token:
        return SourceResult(
            source_name="X legislator list",
            source_url=source_url,
            fetched_at=NOW(),
            freshness="unavailable",
            error="A bearer token with X post-read access is required.",
        )
    query = urlencode(
        {
            "max_results": 25,
            "tweet.fields": "created_at,author_id,public_metrics",
            "expansions": "author_id",
            "user.fields": "name,username",
        }
    )
    endpoint = f"{base_url.rstrip('/')}/2/lists/{LRL_X_LIST_ID}/tweets?{query}"
    body, stale, latency, error = CLIENT.get(
        endpoint, headers={"Authorization": f"Bearer {bearer_token}"}
    )
    posts: list[SocialPost] = []
    if body:
        try:
            payload = requests.models.complexjson.loads(body)
            users = {
                user["id"]: user
                for user in payload.get("includes", {}).get("users", [])
                if user.get("id")
            }
            for post in payload.get("data", []):
                author = users.get(post.get("author_id"), {})
                handle = author.get("username", "")
                metrics = post.get("public_metrics", {})
                posts.append(
                    SocialPost(
                        legislator_name=author.get("name", handle or "Texas legislator"),
                        handle=handle,
                        text=post.get("text", ""),
                        url=(
                            f"https://x.com/{handle}/status/{post['id']}"
                            if handle else f"https://x.com/i/web/status/{post['id']}"
                        ),
                        created_at=safe_datetime(post.get("created_at")),
                        likes=int(metrics.get("like_count", 0)),
                        reposts=int(metrics.get("retweet_count", 0)),
                    )
                )
        except (ValueError, KeyError, TypeError):
            error = "X returned an unreadable or unauthorized list response."
    posts.sort(key=lambda item: item.created_at.timestamp() if item.created_at else 0, reverse=True)
    return _result("X legislator list", source_url, posts, stale, latency, error)


def _parse_twitter_datetime(value: str | None) -> datetime | None:
    """Parse X's timeline timestamp, e.g. 'Sat Jul 04 12:33:41 +0000 2026'."""
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(CENTRAL)


def parse_syndication_timeline(
    body: bytes, account: LegislatorSocialAccount, limit: int = 3
) -> list[SocialPost]:
    """Extract original posts from a public syndication timeline page (no auth)."""
    match = re.search(
        rb'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', body, re.S
    )
    if not match:
        return []
    payload = requests.models.complexjson.loads(match.group(1))
    timeline = (payload.get("props", {}).get("pageProps", {}) or {}).get("timeline", {}) or {}
    posts: list[SocialPost] = []
    for entry in timeline.get("entries", []):
        if entry.get("type") != "tweet":
            continue
        tweet = (entry.get("content") or {}).get("tweet") or {}
        # Keep original posts only — skip retweets and replies.
        if not tweet or tweet.get("retweeted_status") or tweet.get("in_reply_to_status_id_str"):
            continue
        tweet_id = tweet.get("id_str")
        if not tweet_id:
            continue
        user = tweet.get("user") or {}
        handle = user.get("screen_name") or account.handle
        posts.append(
            SocialPost(
                legislator_name=account.name or user.get("name") or handle,
                handle=handle,
                text=clean_text(tweet.get("full_text") or tweet.get("text") or ""),
                url=f"https://x.com/{handle}/status/{tweet_id}",
                created_at=_parse_twitter_datetime(tweet.get("created_at")),
                likes=int(tweet.get("favorite_count") or 0),
                reposts=int(tweet.get("retweet_count") or 0),
            )
        )
        if len(posts) >= limit:
            break
    return posts


def _syndication_account_posts(
    account: LegislatorSocialAccount, per_account: int
) -> tuple[list[SocialPost], bool, int]:
    url = X_SYNDICATION_TIMELINE.format(handle=quote(account.handle, safe=""))
    body, stale, latency, _error = SYNDICATION_CLIENT.get(
        url, headers={"Accept": "text/html,application/xhtml+xml"}
    )
    if not body:
        return [], stale, latency
    try:
        return parse_syndication_timeline(body, account, per_account), stale, latency
    except (ValueError, KeyError, TypeError):
        return [], stale, latency


def _indexed_x_url(handle: str) -> str:
    """Google News' public index of an account's X status pages."""
    return _google_news_url(
        f'site:x.com/{handle}/status "{handle}" when:30d'
    )


def parse_indexed_x_posts(
    body: bytes, account: LegislatorSocialAccount, limit: int = 4
) -> list[SocialPost]:
    """Normalize recently indexed public X posts from a no-token RSS fallback."""
    posts: list[SocialPost] = []
    for entry in _feed_entries(body):
        title, publisher = _publisher_from_title(entry["title"])
        if publisher.lower() not in {"x.com", "twitter.com"}:
            continue
        text = clean_text(title)
        generic_profile = re.search(r"\(@[^)]+\)\s+on\s+X$", text, re.I)
        if not text or not entry["link"] or generic_profile:
            continue
        posts.append(
            SocialPost(
                legislator_name=account.name or account.handle,
                handle=account.handle,
                text=text,
                url=entry["link"],
                created_at=entry["published"],
            )
        )
        if len(posts) >= limit:
            break
    return posts


def _indexed_account_posts(
    account: LegislatorSocialAccount, per_account: int
) -> tuple[list[SocialPost], bool, int]:
    body, stale, latency, _error = CLIENT.get(_indexed_x_url(account.handle))
    if not body:
        return [], stale, latency
    try:
        return parse_indexed_x_posts(body, account, per_account), stale, latency
    except (ET.ParseError, ValueError, TypeError):
        return [], stale, latency


def fetch_public_legislator_posts(
    accounts: list[LegislatorSocialAccount],
    max_accounts: int = PUBLIC_FEED_MAX_ACCOUNTS,
    indexed_max_accounts: int = PUBLIC_INDEX_MAX_ACCOUNTS,
    per_account: int = 3,
    total: int = 24,
) -> SourceResult[SocialPost]:
    """Merge direct and indexed public posts without requiring an X token."""
    source_url = LRL_X_LIST
    syndication_selected = accounts[:max_accounts]
    indexed_selected = accounts[:indexed_max_accounts]
    if not indexed_selected:
        return SourceResult(
            source_name="X public timeline",
            source_url=source_url,
            fetched_at=NOW(),
            freshness="unavailable",
            error="No legislator accounts are available for the public feed.",
        )
    posts: list[SocialPost] = []
    syndication_posts: list[SocialPost] = []
    indexed_posts: list[SocialPost] = []
    syndication_stale = False
    indexed_stale = False
    latency = 0
    # The syndication endpoint supplies direct links and engagement counts, but can
    # return a pinned/old timeline or hit X's anonymous quota. A public search index
    # supplies recent status pages without credentials, so merge both paths.
    with ThreadPoolExecutor(
        max_workers=min(PUBLIC_FEED_CONCURRENCY, len(syndication_selected))
    ) as executor:
        futures = [
            executor.submit(_syndication_account_posts, account, per_account)
            for account in syndication_selected
        ]
        for future in as_completed(futures):
            try:
                account_posts, account_stale, account_latency = future.result()
            except Exception:
                continue
            syndication_posts.extend(account_posts)
            syndication_stale = syndication_stale or account_stale
            latency = max(latency, account_latency)
    with ThreadPoolExecutor(max_workers=min(6, len(indexed_selected))) as executor:
        futures = [
            executor.submit(_indexed_account_posts, account, per_account)
            for account in indexed_selected
        ]
        for future in as_completed(futures):
            try:
                account_posts, account_stale, account_latency = future.result()
            except Exception:
                continue
            indexed_posts.extend(account_posts)
            indexed_stale = indexed_stale or account_stale
            latency = max(latency, account_latency)
    # Prefer the direct X version when both paths contain the same text.
    seen: set[str] = set()
    for item in [*syndication_posts, *indexed_posts]:
        key = (
            item.legislator_name.lower()
            + ":"
            + re.sub(r"[^a-z0-9]", "", item.text.lower())[:180]
        )
        if not key or key in seen:
            continue
        seen.add(key)
        posts.append(item)
    posts.sort(key=lambda item: item.created_at.timestamp() if item.created_at else 0, reverse=True)
    posts = posts[:total]
    has_recent_index = any(
        item.created_at and item.created_at >= NOW() - timedelta(days=31)
        for item in indexed_posts
    )
    stale = indexed_stale if has_recent_index else syndication_stale
    error = ""
    if not posts:
        wait = SYNDICATION_CLIENT.blocked_seconds()
        if wait:
            minutes = max(1, -(-wait // 60))
            error = (
                f"X's public rate limit is active for about {minutes} more minutes. "
                "The feed resumes automatically when the window resets."
            )
        else:
            error = "No public posts were returned for the selected legislators."
    return _result("X public + indexed timelines", source_url, posts, stale, latency, error)


def parse_finance_workbook(body: bytes, source_url: str) -> list[FinanceSummary]:
    workbook = pd.ExcelFile(io.BytesIO(body), engine="openpyxl")
    sheet_name = workbook.sheet_names[0]
    frame = pd.read_excel(workbook, sheet_name=sheet_name)
    frame = frame.rename(columns={"Unnamed: 1": "Rank"})
    required = {
        "Rank",
        "Contributions Received",
        "Political Expenditures",
        "Filer Type",
        "Filer Name",
        "Filer ID",
        "City",
        "State",
    }
    if not required.issubset(frame.columns):
        raise ValueError("Unexpected campaign-finance workbook columns")
    items: list[FinanceSummary] = []
    for _, row in frame.iterrows():
        if pd.isna(row["Filer Name"]):
            continue
        items.append(
            FinanceSummary(
                rank=int(row["Rank"]),
                filer_name=str(row["Filer Name"]),
                filer_type=str(row["Filer Type"]),
                filer_id=str(row["Filer ID"]),
                city="" if pd.isna(row["City"]) else str(row["City"]),
                state="" if pd.isna(row["State"]) else str(row["State"]),
                contributions=float(row["Contributions Received"] or 0),
                expenditures=float(row["Political Expenditures"] or 0),
                reporting_period=f"TEC workbook published {sheet_name.replace('_', ':')}",
                source_url=source_url,
            )
        )
    return items


def fetch_finance() -> SourceResult[FinanceSummary]:
    body, stale, latency, error = CLIENT.get(
        TEC_FINANCE_XLSX, headers={"Referer": "https://www.ethics.state.tx.us/search/cf/"}
    )
    items: list[FinanceSummary] = []
    if body:
        try:
            items = parse_finance_workbook(body, TEC_FINANCE_XLSX)
        except (ValueError, TypeError):
            error = "The TEC workbook format could not be read."
    return _result("Texas Ethics Commission", TEC_FINANCE_HOME, items, stale, latency, error)


def _publisher_from_title(title: str) -> tuple[str, str]:
    if " - " not in title:
        return title, "Unknown publisher"
    story, publisher = title.rsplit(" - ", 1)
    return story.strip(), publisher.strip()


def fetch_headline_feed(name: str, url: str) -> SourceResult[Headline]:
    body, stale, latency, error = CLIENT.get(url)
    items: list[Headline] = []
    if body:
        try:
            for entry in _feed_entries(body):
                title, publisher = _publisher_from_title(entry["title"])
                if name == "Texas Tribune":
                    publisher = "The Texas Tribune"
                elif name in DIRECT_NEWS_FEEDS:
                    publisher = name
                if (name in GOOGLE_NEWS_FEEDS or name == "Texas Tribune") and not is_texas_political_story(
                    title, entry["summary"]
                ):
                    continue
                score = headline_relevance(title, entry["summary"], publisher)
                items.append(
                    Headline(
                        title=title,
                        publisher=publisher,
                        url=entry["link"],
                        published_at=entry["published"],
                        summary=entry["summary"],
                        relevance=score,
                    )
                )
        except (ET.ParseError, ValueError):
            error = "The news feed returned an unreadable response."
    return _result(name, url, items, stale, latency, error)


def parse_texan_headlines(body: bytes) -> list[Headline]:
    soup = BeautifulSoup(body, "html.parser")
    items: list[Headline] = []
    seen: set[str] = set()
    for article in soup.select("article"):
        anchor = article.select_one('a[href*="article_"]')
        if not anchor:
            continue
        url = urljoin(THE_TEXAN_HOME, anchor.get("href", ""))
        if url in seen:
            continue
        title = clean_text(
            anchor.get("aria-label")
            or anchor.get_text(" ", strip=True)
            or (article.select_one("h1, h2, h3, h4").get_text(" ", strip=True)
                if article.select_one("h1, h2, h3, h4") else "")
        )
        if not title:
            continue
        time_node = article.select_one("time[datetime]")
        published = safe_datetime(time_node.get("datetime")) if time_node else None
        summary_node = article.select_one(".summary, .card-summary, p")
        summary = clean_text(summary_node.get_text(" ", strip=True)) if summary_node else ""
        relevance = headline_relevance(title, summary, "The Texan")
        seen.add(url)
        items.append(
            Headline(
                title=title,
                publisher="The Texan",
                url=url,
                published_at=published,
                summary=summary,
                relevance=relevance,
            )
        )
    items.sort(key=lambda item: item.published_at.timestamp() if item.published_at else 0, reverse=True)
    return items


def fetch_texan_headlines() -> SourceResult[Headline]:
    body, stale, latency, error = CLIENT.get(THE_TEXAN_HOME)
    items: list[Headline] = []
    if body:
        try:
            items = parse_texan_headlines(body)
        except Exception:
            error = "The Texan homepage format could not be read."
    return _result("The Texan", THE_TEXAN_HOME, items, stale, latency, error)


def parse_bullpen_daily(body: bytes) -> list[Headline]:
    soup = BeautifulSoup(body, "html.parser")
    items: list[Headline] = []
    seen: set[str] = set()
    for anchor in soup.select('a[href*="/daily-bull/"]'):
        title = clean_text(anchor.get_text(" ", strip=True))
        url = urljoin(TEXAS_BULLPEN_DAILY, anchor.get("href", ""))
        if not title or len(title) < 20 or url.rstrip("/") == TEXAS_BULLPEN_DAILY.rstrip("/"):
            continue
        if url in seen:
            continue
        container = anchor
        for _ in range(6):
            container = container.parent
            if container is None:
                break
            if re.search(
                r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
                r"\s+\d{1,2},\s+\d{4}\b",
                container.get_text(" ", strip=True),
            ):
                break
        context = clean_text(container.get_text(" ", strip=True)) if container else ""
        date_match = re.search(
            r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2},\s+\d{4}\b",
            context,
        )
        published = (
            datetime.strptime(date_match.group(0), "%B %d, %Y").replace(tzinfo=CENTRAL)
            if date_match
            else None
        )
        seen.add(url)
        items.append(
            Headline(
                title=title,
                publisher="Texas Bullpen",
                url=url,
                published_at=published,
                relevance=headline_relevance(title, context, "Texas Bullpen"),
            )
        )
    items.sort(key=lambda item: item.published_at.timestamp() if item.published_at else 0, reverse=True)
    return items


def fetch_bullpen_daily() -> SourceResult[Headline]:
    body, stale, latency, error = CLIENT.get(TEXAS_BULLPEN_DAILY)
    items: list[Headline] = []
    if body:
        try:
            items = parse_bullpen_daily(body)
        except Exception:
            error = "The Daily Bull page format could not be read."
    return _result("Texas Bullpen", TEXAS_BULLPEN_DAILY, items, stale, latency, error)


def fetch_headlines() -> list[SourceResult[Headline]]:
    calls: list[tuple[str, Callable[[], SourceResult[Headline]]]] = [
        ("Texas Tribune", lambda: fetch_headline_feed("Texas Tribune", TRIBUNE_FEED)),
        ("The Texan", fetch_texan_headlines),
        ("Texas Bullpen", fetch_bullpen_daily),
    ]
    calls.extend(
        (name, lambda n=name, u=url: fetch_headline_feed(n, u))
        for name, url in DIRECT_NEWS_FEEDS.items()
    )
    calls.extend(
        (
            name,
            lambda n=name, u=url: fetch_headline_feed(n, u),
        )
        for name, url in GOOGLE_NEWS_FEEDS.items()
    )
    return _parallel(calls)


def dedupe_headlines(results: Iterable[SourceResult[Headline]]) -> list[Headline]:
    best_by_title: dict[str, Headline] = {}
    token_sets: dict[str, set[str]] = {}
    normalized_titles: dict[str, str] = {}
    stop = {"the", "a", "an", "to", "of", "in", "on", "for", "and", "with", "as", "at"}
    for item in [x for result in results for x in result.items]:
        tokens = {
            token for token in re.findall(r"[a-z0-9]+", item.title.lower())
            if token not in stop and len(token) > 1
        }
        key = " ".join(sorted(tokens))
        normalized = " ".join(re.findall(r"[a-z0-9]+", item.title.lower()))
        # Collapse near-identical syndicated headlines, not just punctuation variants.
        for candidate, candidate_tokens in token_sets.items():
            union = tokens | candidate_tokens
            overlap = len(tokens & candidate_tokens)
            similar_words = union and overlap / len(union) >= 0.82
            similar_phrasing = (
                overlap >= 5
                and SequenceMatcher(
                    None, normalized, normalized_titles.get(candidate, "")
                ).ratio()
                >= 0.70
            )
            if similar_words or similar_phrasing:
                key = candidate
                break
        token_sets.setdefault(key, tokens)
        normalized_titles.setdefault(key, normalized)
        existing = best_by_title.get(key)
        item_rank = (
            item.relevance,
            item.published_at.timestamp() if item.published_at else 0,
        )
        existing_rank = (
            existing.relevance if existing else -1,
            existing.published_at.timestamp() if existing and existing.published_at else 0,
        )
        if existing is None or item_rank > existing_rank:
            best_by_title[key] = item
    output = list(best_by_title.values())
    cutoff = NOW() - timedelta(days=7)
    fresh = [item for item in output if item.published_at and item.published_at >= cutoff]
    if fresh:
        requested_publishers = {"The Texan", "Texas Scorecard", "Current Revolt", "Texas Bullpen"}
        retained = {item.url: item for item in fresh}
        for publisher in requested_publishers:
            publisher_items = sorted(
                (item for item in output if item.publisher == publisher),
                key=lambda item: item.published_at.timestamp() if item.published_at else 0,
                reverse=True,
            )
            for item in publisher_items[:3]:
                retained.setdefault(item.url, item)
        output = list(retained.values())
    output.sort(
        key=lambda item: (
            headline_priority(item),
            item.relevance,
            item.published_at.timestamp() if item.published_at else 0,
        ),
        reverse=True,
    )
    return output


def _local_date(value: str, formats: tuple[str, ...]) -> datetime | None:
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=CENTRAL)
        except (TypeError, ValueError):
            continue
    return None


def parse_open_meetings(body: bytes, limit: int = 300) -> list[GovernmentRecord]:
    """Parse the Secretary of State's daily backup of pending meeting notices."""
    text = BeautifulSoup(body, "html.parser").get_text("\n", strip=True)
    labels = (
        "Status",
        "TRD",
        "Related TRD",
        "Submitted Date/Time",
        "Agency Name",
        "Board",
        "Committee",
        "Meeting Date",
        "Meeting Time",
        "Address",
        "City",
        "State",
        "Additional Information",
        "Emergency Meeting",
        "Emergency Reason",
        "Agenda",
    )
    marker = "|".join(re.escape(label) for label in labels)
    records: list[GovernmentRecord] = []
    seen: set[str] = set()
    for block in re.split(r"={20,}", text):
        values: dict[str, str] = {}
        for label in labels:
            match = re.search(
                rf"(?:^|\n){re.escape(label)}:\s*(.*?)(?=\n(?:{marker}):|\Z)",
                block,
                re.S,
            )
            values[label] = clean_text(match.group(1)) if match else ""
        trd = values["TRD"]
        agency = values["Agency Name"]
        meeting_date = values["Meeting Date"]
        if not trd or not agency or not meeting_date or trd in seen:
            continue
        starts_at = _local_date(
            f"{meeting_date} {values['Meeting Time'].replace('(Local Time)', '').strip()}",
            ("%m/%d/%Y %I:%M %p", "%m/%d/%Y"),
        )
        title = next(
            (
                candidate
                for candidate in (values["Board"], values["Committee"], agency)
                if candidate and candidate.upper() != "N/A"
            ),
            agency,
        )
        agenda = values["Agenda"]
        location = ", ".join(
            value
            for value in (values["Address"], values["City"], values["State"])
            if value and value.upper() != "N/A"
        )
        summary = agenda[:420]
        if location:
            summary = f"{location} — {summary}" if summary else location
        status = values["Status"] or "Filed"
        if values["Emergency Meeting"].lower() == "yes":
            status = f"{status} · Emergency"
        submitted = _local_date(
            re.sub(r"\s+C[DS]T$", "", values["Submitted Date/Time"]),
            ("%m/%d/%Y %I:%M %p",),
        )
        records.append(
            GovernmentRecord(
                title=title,
                category="Open meeting",
                agency=agency,
                url=OPEN_MEETINGS_PRIMARY,
                published_at=submitted,
                occurs_at=starts_at,
                summary=summary,
                status=status,
                identifier=trd,
            )
        )
        seen.add(trd)
        if len(records) >= limit:
            break
    records.sort(
        key=lambda item: item.occurs_at.timestamp() if item.occurs_at else float("inf")
    )
    return records


def fetch_open_meetings() -> SourceResult[GovernmentRecord]:
    body, stale, latency, error = CLIENT.get(OPEN_MEETINGS_BACKUP)
    items: list[GovernmentRecord] = []
    if body:
        try:
            items = parse_open_meetings(body)
        except Exception:
            error = "The statewide open-meetings bulletin format could not be read."
    return _result(
        "Texas statewide open meetings",
        OPEN_MEETINGS_BACKUP,
        items,
        stale,
        latency,
        error,
    )


def parse_texas_register(body: bytes, source_url: str = TEXAS_REGISTER_CURRENT) -> list[GovernmentRecord]:
    """Normalize the current Texas Register table of contents."""
    soup = BeautifulSoup(body, "html.parser")
    issue_text = soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else ""
    issue_match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},\s+\d{4}",
        issue_text,
    )
    published = (
        _local_date(issue_match.group(0), ("%B %d, %Y",)) if issue_match else None
    )
    category = ""
    agency = ""
    records: list[GovernmentRecord] = []
    seen: set[tuple[str, str]] = set()
    rule_sections = {"PROPOSED RULES", "ADOPTED RULES", "REVIEW OF AGENCY RULES"}
    for anchor in soup.select("a[href]"):
        heading = anchor.find_previous("h3")
        next_category = (
            clean_text(heading.get_text(" ", strip=True)).upper() if heading else ""
        )
        if next_category != category:
            category = next_category
            agency = ""
        title = clean_text(anchor.get_text(" ", strip=True))
        is_rule = bool(re.search(r"\b\d+\s+TAC\s+§", title, re.I))
        if (
            title
            and title == title.upper()
            and not is_rule
            and category in rule_sections
            and anchor.parent is not None
            and anchor.parent.name == "p"
            and len(title) > 4
        ):
            agency = title
            continue
        url = urljoin(source_url, anchor["href"])
        is_front_matter = category in {"THE GOVERNOR", "ATTORNEY GENERAL"}
        if not title or not (is_rule or is_front_matter):
            continue
        key = (title, url)
        if key in seen:
            continue
        record_category = category.title() if category else "Texas Register"
        status = {
            "PROPOSED RULES": "Proposed",
            "ADOPTED RULES": "Adopted",
            "REVIEW OF AGENCY RULES": "Under review",
        }.get(category, "Published")
        container = anchor.find_parent("div")
        container_text = (
            clean_text(container.get_text(" ", strip=True)) if container else ""
        )
        trd_matches = re.findall(r"\bTRD-\d+\b", container_text)
        identifier = (
            f"{trd_matches[-1]} · {title}"
            if is_rule and trd_matches
            else title
            if is_rule
            else ""
        )
        records.append(
            GovernmentRecord(
                title=title,
                category=record_category,
                agency=agency or category.title() or "Texas Register",
                url=url,
                published_at=published,
                summary=f"{issue_text} · Official Texas Register filing.",
                status=status,
                identifier=identifier,
            )
        )
        seen.add(key)
    return records


def fetch_texas_register() -> SourceResult[GovernmentRecord]:
    body, stale, latency, error = CLIENT.get(TEXAS_REGISTER_CURRENT)
    items: list[GovernmentRecord] = []
    if body:
        try:
            items = parse_texas_register(body)
        except Exception:
            error = "The current Texas Register issue could not be read."
    return _result(
        "Texas Register",
        TEXAS_REGISTER_CURRENT,
        items,
        stale,
        latency,
        error,
    )


def _money_number(value: str) -> float:
    try:
        return float(re.sub(r"[^0-9.-]", "", value))
    except (TypeError, ValueError):
        return 0.0


def parse_direct_expenditures(body: bytes, limit: int = 250) -> list[GovernmentRecord]:
    """Parse TEC's current direct-campaign-expenditure report without the 1 GB archive."""
    records: list[GovernmentRecord] = []
    seen: set[str] = set()
    for frame in pd.read_html(io.BytesIO(body)):
        for _, row in frame.iterrows():
            values: list[str] = []
            for raw in row.tolist():
                value = clean_text("" if pd.isna(raw) else str(raw))
                if value and value.lower() != "nan" and value not in values:
                    values.append(value)
            date_value = next(
                (value for value in values if re.fullmatch(r"\d{2}/\d{2}/\d{4}", value)),
                "",
            )
            money = [value for value in values if re.fullmatch(r"\$[\d,]+\.\d{2}|\$0", value)]
            identifiers = next(
                (
                    value
                    for value in values
                    if re.fullmatch(r"\d{8,9}\s+\d{8}", value)
                ),
                "",
            )
            if not date_value or len(money) < 2 or not identifiers:
                continue
            id_index = values.index(identifiers)
            name = values[id_index + 1] if id_index + 1 < len(values) else ""
            if not name or identifiers in seen:
                continue
            unitemized, itemized = money[0], money[1]
            total = _money_number(unitemized) + _money_number(itemized)
            records.append(
                GovernmentRecord(
                    title=name,
                    category="Direct campaign expenditure",
                    agency="Texas Ethics Commission",
                    url=TEC_DIRECT_EXPENDITURES,
                    published_at=_local_date(date_value, ("%m/%d/%Y",)),
                    summary=f"Itemized {itemized} · Unitemized {unitemized}",
                    status="Filed",
                    identifier=identifiers,
                    value=f"${total:,.2f}",
                )
            )
            seen.add(identifiers)
    records.sort(
        key=lambda item: item.published_at.timestamp() if item.published_at else 0,
        reverse=True,
    )
    return records[:limit]


def fetch_direct_expenditures() -> SourceResult[GovernmentRecord]:
    body, stale, latency, error = CLIENT.get(TEC_DIRECT_EXPENDITURES)
    items: list[GovernmentRecord] = []
    if body:
        try:
            items = parse_direct_expenditures(body)
        except Exception:
            error = "The current TEC direct-expenditure report could not be read."
    return _result(
        "TEC direct expenditures",
        TEC_DIRECT_EXPENDITURES,
        items,
        stale,
        latency,
        error,
    )


LOBBY_COMPENSATION = {
    "LOBBCOMPEQZERO": "$0",
    "LOBBCOMP01": "Under $22,890",
    "LOBBCOMP02": "$22,890–$57,219.99",
    "LOBBCOMP03": "$57,220–$114,429.99",
    "LOBBCOMP04": "$114,430–$228,869.99",
    "LOBBCOMP05": "$228,870–$343,299.99",
    "LOBBCOMP06": "$343,300–$457,729.99",
    "LOBBCOMP07": "$457,730–$572,159.99",
    "LOBBCOMP08": "$572,160–$686,599.99",
    "LOBBCOMP09": "$686,600–$801,029.99",
    "LOBBCOMP10": "$801,030–$915,459.99",
    "LOBBCOMP11": "$915,460–$1,029,889.99",
    "LOBBCOMP12": "$1,029,890–$1,144,329.99",
    "LOBBCOMP13": "$1,144,330 or more",
    "LOBBCOMPEXACTAMT": "Exact amount disclosed",
}


def parse_lobby_roster(body: bytes, limit: int = 10000) -> list[GovernmentRecord]:
    frame = pd.read_excel(io.BytesIO(body))
    required = {"Client Name", "FilerID", "Lobby Name", "Start", "Stop", "Method Payment", "Amount"}
    if not required.issubset(frame.columns):
        raise ValueError("The TEC lobby roster schema changed.")
    records: list[GovernmentRecord] = []
    today = NOW().date()
    for _, row in frame.iterrows():
        client = clean_text(str(row.get("Client Name", "")))
        lobbyist = clean_text(str(row.get("Lobby Name", "")))
        if not client or not lobbyist or client.lower() == "nan" or lobbyist.lower() == "nan":
            continue
        start = _local_date(str(row.get("Start", "")), ("%m/%d/%Y",))
        stop = _local_date(str(row.get("Stop", "")), ("%m/%d/%Y",))
        status = "Active" if not stop or stop.date() >= today else "Terminated"
        method = clean_text(str(row.get("Method Payment", "")))
        code = clean_text(str(row.get("Amount", "")))
        filer_id = clean_text(str(row.get("FilerID", "")))
        records.append(
            GovernmentRecord(
                title=f"{client} — {lobbyist}",
                category="Lobby registration",
                agency="Texas Ethics Commission",
                url=TEC_LOBBY_HOME,
                published_at=start,
                occurs_at=stop,
                summary=f"{method.title()} compensation disclosure" if method else "Lobby registration",
                status=status,
                identifier=filer_id,
                value=LOBBY_COMPENSATION.get(code, code),
            )
        )
    records.sort(
        key=lambda item: (
            item.status == "Active",
            item.published_at.timestamp() if item.published_at else 0,
        ),
        reverse=True,
    )
    return records[:limit]


def fetch_lobby_roster() -> SourceResult[GovernmentRecord]:
    body, stale, latency, error = CLIENT.get(TEC_LOBBY_ROSTER)
    items: list[GovernmentRecord] = []
    if body:
        try:
            items = parse_lobby_roster(body)
        except Exception:
            error = "The current TEC lobby-registration workbook could not be read."
    return _result(
        "TEC lobby registrations",
        TEC_LOBBY_ROSTER,
        items,
        stale,
        latency,
        error,
    )


def parse_lobby_activity(body: bytes, limit: int = 10000) -> list[GovernmentRecord]:
    soup = BeautifulSoup(body, "html.parser")
    table = soup.select_one("table.jrPage")
    if not table:
        return []
    records: list[GovernmentRecord] = []
    lobbyist = ""
    filer_id = ""
    for row in table.select("tr"):
        values: list[str] = []
        for cell in row.select(":scope > td"):
            value = clean_text(cell.get_text(" ", strip=True))
            if value and value not in values:
                values.append(value)
        if (
            len(values) == 2
            and re.fullmatch(r"\d{8}", values[0])
            and not values[1].startswith("LOBBYACT")
        ):
            filer_id, lobbyist = values
            continue
        if (
            len(values) < 4
            or not lobbyist
            or not re.fullmatch(r"\d{9}", values[0])
            or not values[1].startswith("LOBBYACT")
        ):
            continue
        report_id, report_type, filed_text, covering = values[:4]
        filed_match = re.search(r"\d{2}/\d{2}/\d{4}", filed_text)
        records.append(
            GovernmentRecord(
                title=f"{lobbyist} — {report_type}",
                category="Lobby activity report",
                agency="Texas Ethics Commission",
                url=TEC_LOBBY_ACTIVITY,
                published_at=(
                    _local_date(filed_match.group(0), ("%m/%d/%Y",))
                    if filed_match
                    else None
                ),
                summary=covering,
                status="Filed",
                identifier=f"{filer_id} · {report_id}",
            )
        )
        if len(records) >= limit:
            break
    records.sort(
        key=lambda item: item.published_at.timestamp() if item.published_at else 0,
        reverse=True,
    )
    return records


def fetch_lobby_activity() -> SourceResult[GovernmentRecord]:
    body, stale, latency, error = CLIENT.get(TEC_LOBBY_ACTIVITY)
    items: list[GovernmentRecord] = []
    if body:
        try:
            items = parse_lobby_activity(body)
        except Exception:
            error = "The current TEC lobby-activity report list could not be read."
    return _result(
        "TEC lobby activity reports",
        TEC_LOBBY_ACTIVITY,
        items,
        stale,
        latency,
        error,
    )


def parse_governor_actions(body: bytes, now: datetime | None = None) -> list[GovernmentRecord]:
    soup = BeautifulSoup(body, "html.parser")
    current = now or NOW()
    records: list[GovernmentRecord] = []
    seen: set[str] = set()
    for block in soup.select("div.media-object.m-b-4"):
        anchor = block.select_one('a[href*="/news/post/"]')
        if not anchor:
            continue
        title = clean_text(anchor.get_text(" ", strip=True))
        url = urljoin(GOVERNOR_NEWS, anchor.get("href", ""))
        if not title or url in seen:
            continue
        text = clean_text(block.get_text(" ", strip=True))
        date_match = re.search(
            r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\b",
            text,
        )
        published = (
            _local_date(f"{date_match.group(0)} {current.year}", ("%b %d %Y",))
            if date_match
            else None
        )
        if published and published > current + timedelta(days=7):
            published = published.replace(year=published.year - 1)
        categories = [
            value
            for value in ("Appointment", "Proclamation", "Legislative Statement", "Press Release")
            if value.lower() in text.lower()
        ]
        category = categories[0] if categories else "Governor action"
        records.append(
            GovernmentRecord(
                title=title,
                category=category,
                agency="Office of the Governor",
                url=url,
                published_at=published,
                summary=" · ".join(categories) or "Official governor news release",
                status="Published",
            )
        )
        seen.add(url)
    return records


def fetch_governor_actions() -> SourceResult[GovernmentRecord]:
    body, stale, latency, error = CLIENT.get(GOVERNOR_NEWS)
    items: list[GovernmentRecord] = []
    if body:
        try:
            items = parse_governor_actions(body)
        except Exception:
            error = "The governor's current action feed could not be read."
    return _result(
        "Governor actions",
        GOVERNOR_NEWS,
        items,
        stale,
        latency,
        error,
    )


def parse_court_order_page(
    body: bytes, source_url: str, pronounced_at: datetime | None
) -> list[GovernmentRecord]:
    text = BeautifulSoup(body, "html.parser").get_text("\n", strip=True)
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    records: list[GovernmentRecord] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\d{2}-\d{4})(?:\s+(.+))?$", line)
        if not match:
            continue
        case_id, inline_title = match.groups()
        title = inline_title or (lines[index + 1] if index + 1 < len(lines) else case_id)
        context_start = index + 1 if inline_title else index + 2
        context = " ".join(lines[context_start : context_start + 3])
        records.append(
            GovernmentRecord(
                title=title[:300],
                category="Court order or opinion",
                agency="Supreme Court of Texas",
                url=source_url,
                published_at=pronounced_at,
                summary=context[:420],
                status="Issued",
                identifier=case_id,
            )
        )
    if not records and pronounced_at:
        records.append(
            GovernmentRecord(
                title=f"Supreme Court orders pronounced {pronounced_at.strftime('%B %d, %Y')}",
                category="Court orders",
                agency="Supreme Court of Texas",
                url=source_url,
                published_at=pronounced_at,
                summary="Official orders and opinions issued by the Supreme Court of Texas.",
                status="Issued",
            )
        )
    return records


def fetch_court_activity(days: int = 5) -> SourceResult[GovernmentRecord]:
    body, stale, latency, error = CLIENT.get(SUPREME_COURT_ORDERS)
    items: list[GovernmentRecord] = []
    if body:
        try:
            soup = BeautifulSoup(body, "html.parser")
            dated: list[tuple[datetime, str]] = []
            for anchor in soup.select('a[href*="/orders-opinions/"]'):
                published = _local_date(
                    clean_text(anchor.get_text(" ", strip=True)), ("%B %d, %Y",)
                )
                if published:
                    dated.append((published, urljoin(SUPREME_COURT_ORDERS, anchor["href"])))
            dated = sorted(set(dated), key=lambda item: item[0], reverse=True)[:days]
            for published, url in dated:
                page, page_stale, page_latency, _ = CLIENT.get(url)
                stale = stale or page_stale
                latency = max(latency, page_latency)
                if page:
                    items.extend(parse_court_order_page(page, url, published))
        except Exception:
            error = "The current Texas Supreme Court order pages could not be read."
    items.sort(
        key=lambda item: item.published_at.timestamp() if item.published_at else 0,
        reverse=True,
    )
    return _result(
        "Texas Supreme Court",
        SUPREME_COURT_ORDERS,
        items,
        stale,
        latency,
        error,
    )


def parse_election_data_sources(body: bytes) -> list[GovernmentRecord]:
    soup = BeautifulSoup(body, "html.parser")
    wanted = (
        "Early Voting Turnout (current)",
        "Election Day Voting Turnout",
        "Historical Election Results",
        "Voter Registration Figures",
        "Voter Registration and Unofficial Early Voting Figures by County",
    )
    records: list[GovernmentRecord] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        title = clean_text(anchor.get_text(" ", strip=True))
        if not any(value.lower() in title.lower() for value in wanted):
            continue
        url = urljoin(ELECTION_DATA_HOME, anchor["href"])
        if url in seen:
            continue
        status = "Unofficial when active" if "unofficial" in title.lower() or "turnout" in title.lower() else "Official"
        records.append(
            GovernmentRecord(
                title=title,
                category="Election and turnout data",
                agency="Texas Secretary of State",
                url=url,
                summary="Current statewide election, turnout, or registration data product.",
                status=status,
            )
        )
        seen.add(url)
    return records


def fetch_election_data() -> SourceResult[GovernmentRecord]:
    body, stale, latency, error = CLIENT.get(ELECTION_DATA_HOME)
    items: list[GovernmentRecord] = []
    if body:
        try:
            items = parse_election_data_sources(body)
        except Exception:
            error = "The Secretary of State election-data index could not be read."
    return _result(
        "Texas election data",
        ELECTION_DATA_HOME,
        items,
        stale,
        latency,
        error,
    )


def parse_contracts(body: bytes, limit: int = 2000) -> list[GovernmentRecord]:
    soup = BeautifulSoup(body, "html.parser")
    records: list[GovernmentRecord] = []
    seen: set[tuple[str, str, str]] = set()
    for row in soup.select("table tr"):
        cells = [
            clean_text(cell.get_text(" ", strip=True))
            for cell in row.select("th,td")
        ]
        if len(cells) < 4:
            continue
        supplier, purchase_order, description, amount = cells[:4]
        if supplier.lower() in {"supplier", "supplier name"} or purchase_order.lower() == "po":
            continue
        key = (supplier, purchase_order, description)
        if not supplier or not description or key in seen:
            continue
        records.append(
            GovernmentRecord(
                title=description,
                category="State contract",
                agency="Texas Comptroller of Public Accounts",
                url=COMPTROLLER_CONTRACTS,
                summary=f"{supplier} · Purchase order {purchase_order}",
                status="Active listing",
                identifier=purchase_order,
                value=amount,
            )
        )
        seen.add(key)
        if len(records) >= limit:
            break
    return records


def fetch_contracts() -> SourceResult[GovernmentRecord]:
    body, stale, latency, error = CLIENT.get(COMPTROLLER_CONTRACTS)
    items: list[GovernmentRecord] = []
    if body:
        try:
            items = parse_contracts(body)
        except Exception:
            error = "The Comptroller's current public-contract listing could not be read."
    return _result(
        "Texas public contracts",
        COMPTROLLER_CONTRACTS,
        items,
        stale,
        latency,
        error,
    )


def parse_budget_updates(body: bytes) -> list[GovernmentRecord]:
    soup = BeautifulSoup(body, "html.parser")
    records: list[GovernmentRecord] = []
    for anchor in soup.select(".documentslist a.recentDocsLink[href]"):
        title = clean_text(anchor.get_text(" ", strip=True))
        detail = anchor.find_next_sibling("span")
        detail_text = clean_text(detail.get_text(" ", strip=True)) if detail else ""
        date_match = re.search(
            r"(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2},\s+\d{4}",
            detail_text,
        )
        published = (
            _local_date(date_match.group(0), ("%B %d, %Y",)) if date_match else None
        )
        document_type = (
            detail_text.split("|", 1)[1].strip()
            if "|" in detail_text
            else "Budget publication"
        )
        records.append(
            GovernmentRecord(
                title=title,
                category="Budget and fiscal update",
                agency="Legislative Budget Board",
                url=urljoin(LBB_HOME, anchor["href"]),
                published_at=published,
                summary=document_type,
                status="Published",
            )
        )
    return records


def fetch_budget_updates() -> SourceResult[GovernmentRecord]:
    body, stale, latency, error = CLIENT.get(LBB_HOME)
    items: list[GovernmentRecord] = []
    if body:
        try:
            items = parse_budget_updates(body)
        except Exception:
            error = "The Legislative Budget Board's current publications could not be read."
    return _result(
        "Legislative Budget Board",
        LBB_HOME,
        items,
        stale,
        latency,
        error,
    )


def fetch_vote_sources() -> SourceResult[GovernmentRecord]:
    """Expose official vote and amendment search surfaces as live source records."""
    items = [
        GovernmentRecord(
            title=name,
            category="Legislative votes and amendments",
            agency="Texas Legislature Online",
            url=url,
            summary="Official current-session search maintained by Texas Legislature Online.",
            status="Available",
        )
        for name, url in TLO_VOTE_SOURCES.items()
    ]
    return SourceResult(
        source_name="TLO votes and amendments",
        source_url="https://capitol.texas.gov/BillLookup/VoteInfo.aspx",
        items=items,
        fetched_at=NOW(),
        freshness="live",
    )


def fetch_regulatory_dockets() -> SourceResult[GovernmentRecord]:
    """Discover current regulatory matters from the official Texas Register issue."""
    register = fetch_texas_register()
    keywords = (
        "PUBLIC UTILITY COMMISSION",
        "RAILROAD COMMISSION",
        "COMMISSION ON ENVIRONMENTAL QUALITY",
        "DEPARTMENT OF INSURANCE",
    )
    items = [
        GovernmentRecord(
            title=item.title,
            category="Regulatory docket",
            agency=item.agency,
            url=item.url,
            published_at=item.published_at,
            summary=item.summary,
            status=item.status,
            identifier=item.identifier,
        )
        for item in register.items
        if any(keyword in item.agency.upper() for keyword in keywords)
    ]
    return SourceResult(
        source_name="Texas regulatory dockets",
        source_url=TEXAS_REGISTER_CURRENT,
        items=items,
        fetched_at=register.fetched_at,
        freshness=register.freshness,
        latency_ms=register.latency_ms,
        error=register.error,
    )


def fetch_government_intelligence() -> list[SourceResult[GovernmentRecord]]:
    calls: list[tuple[str, Callable[[], SourceResult[GovernmentRecord]]]] = [
        ("Open meetings", fetch_open_meetings),
        ("Texas Register", fetch_texas_register),
        ("Governor actions", fetch_governor_actions),
        ("Regulatory dockets", fetch_regulatory_dockets),
        ("Courts", fetch_court_activity),
        ("Election data", fetch_election_data),
        ("Budget updates", fetch_budget_updates),
        ("Votes", fetch_vote_sources),
    ]
    return _parallel(calls)


def fetch_influence_intelligence() -> list[SourceResult[GovernmentRecord]]:
    return _parallel(
        [
            ("Direct expenditures", fetch_direct_expenditures),
            ("Lobby registrations", fetch_lobby_roster),
            ("Lobby activity", fetch_lobby_activity),
            ("Public contracts", fetch_contracts),
        ]
    )


def _event_type(title: str) -> str:
    lowered = title.lower()
    if "women" in lowered:
        return "Republican women"
    if "young republican" in lowered or " yr" in lowered:
        return "Young Republicans"
    if "fundrais" in lowered or "reception" in lowered:
        return "Fundraiser"
    if "training" in lowered:
        return "Training"
    if "meeting" in lowered or "club" in lowered:
        return "Club meeting"
    return "Party event"


def fetch_tribe_events(source: dict[str, str]) -> SourceResult[PoliticalEvent]:
    query = urlencode(
        {
            "start_date": date.today().isoformat(),
            "end_date": (date.today() + timedelta(days=120)).isoformat(),
            "per_page": 50,
        }
    )
    body, stale, latency, error = CLIENT.get(f"{source['url']}?{query}")
    items: list[PoliticalEvent] = []
    if body:
        try:
            payload = requests.models.complexjson.loads(body)
            for raw in payload.get("events", []):
                venue = raw.get("venue") or {}
                title = clean_text(raw.get("title", ""))
                organizers = raw.get("organizer") or []
                organizer = next(
                    (
                        clean_text(value.get("organizer", ""))
                        for value in organizers
                        if isinstance(value, dict) and value.get("organizer")
                    ),
                    source["name"],
                )
                items.append(
                    PoliticalEvent(
                        title=title,
                        region=source["region"],
                        organizer=organizer,
                        event_type=_event_type(title),
                        url=raw.get("url", source["page"]),
                        starts_at=safe_datetime(raw.get("start_date")),
                        ends_at=safe_datetime(raw.get("end_date")),
                        venue=clean_text(venue.get("venue", "")),
                        address=clean_text(
                            " ".join(
                                str(venue.get(key, ""))
                                for key in ("address", "city", "state", "zip")
                            )
                        ),
                    )
                )
        except (ValueError, TypeError):
            error = "The event API returned an unreadable response."
    return _result(source["name"], source["page"], items, stale, latency, error)


def _event_datetime(date_text: str, time_text: str = "") -> datetime | None:
    cleaned_date = clean_text(date_text)
    cleaned_time = clean_text(time_text).upper().replace(".", "")
    match = re.search(
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?[,]?\s*"
        r"((?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},\s+\d{4})",
        cleaned_date,
        re.I,
    )
    if not match:
        return None
    for pattern in ("%B %d, %Y %I:%M%p", "%B %d, %Y %I%p"):
        try:
            return datetime.strptime(
                f"{match.group(1).title()} {cleaned_time.replace(' ', '')}", pattern
            ).replace(tzinfo=CENTRAL)
        except ValueError:
            continue
    try:
        return datetime.strptime(match.group(1).title(), "%B %d, %Y").replace(tzinfo=CENTRAL)
    except ValueError:
        return None


def fetch_html_events(source: dict[str, str]) -> SourceResult[PoliticalEvent]:
    body, stale, latency, error = CLIENT.get(source["url"])
    items: list[PoliticalEvent] = []
    if body:
        try:
            soup = BeautifulSoup(body, "html.parser")
            candidates = soup.select(
                ".simcal-event, article, .event:not(.calendar), .tribe-events-calendar-list__event-row, "
                "[class*='adrsscntnt']"
            )
            for node in candidates[:60]:
                title_node = node.select_one(
                    ".simcal-event-title, .event-title, h1, h2, h3, h4, h5, h6, "
                    ".tribe-events-calendar-list__event-title"
                )
                anchor = (
                    title_node.select_one("a[href]")
                    if title_node
                    else node.select_one("a[href]")
                ) or node.select_one(".event-link a[href], a[href]")
                title = clean_text(title_node.get_text(" ", strip=True)) if title_node else ""
                if not title and anchor:
                    title = clean_text(anchor.get_text(" ", strip=True))
                if len(title) < 5 or title.lower() in {
                    "events",
                    "event",
                    "calendar",
                    "event calendar",
                    "upcoming events",
                }:
                    continue
                time_node = node.select_one("time, [itemprop='startDate'][content]")
                end_node = node.select_one("[itemprop='endDate'][content]")
                date_node = node.select_one(".event-date, [class*='date']")
                hours_node = node.select_one(".event-hours, [class*='time']")
                starts = (
                    safe_datetime(time_node.get("datetime") or time_node.get("content"))
                    if time_node
                    else None
                )
                if not starts:
                    starts = _event_datetime(
                        date_node.get_text(" ", strip=True) if date_node else node.get_text(" ", strip=True),
                        hours_node.get_text(" ", strip=True) if hours_node else "",
                    )
                if not starts:
                    continue
                if starts and starts < NOW() - timedelta(days=1):
                    continue
                venue_node = node.select_one(
                    ".event-venue, [class*='venue'], .simcal-event-address"
                )
                ends = (
                    safe_datetime(end_node.get("content"))
                    if end_node
                    else None
                )
                items.append(
                    PoliticalEvent(
                        title=title,
                        region=source["region"],
                        organizer=source["name"],
                        event_type=_event_type(title),
                        url=urljoin(
                            source["url"],
                            anchor.get("href", source["page"]) if anchor else source["page"],
                        ),
                        starts_at=starts,
                        ends_at=ends,
                        venue=clean_text(venue_node.get_text(" ", strip=True)) if venue_node else "",
                    )
                )
        except Exception:
            error = "The event page format could not be read."
    return _result(source["name"], source["page"], items, stale, latency, error)


def _ics_value(lines: list[str], key: str) -> str:
    for line in lines:
        field = line.split(":", 1)[0].split(";", 1)[0].upper()
        if field == key and ":" in line:
            return line.split(":", 1)[1]
    return ""


def _ics_datetime(value: str) -> datetime | None:
    value = value.strip()
    for pattern in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            parsed = datetime.strptime(value, pattern)
            if pattern.endswith("Z"):
                return parsed.replace(tzinfo=ZoneInfo("UTC")).astimezone(CENTRAL)
            return parsed.replace(tzinfo=CENTRAL)
        except ValueError:
            continue
    return None


def parse_ics_events(body: bytes, source: dict[str, str]) -> list[PoliticalEvent]:
    text = body.decode("utf-8", errors="replace").replace("\r\n", "\n")
    text = re.sub(r"\n[ \t]", "", text)
    output: list[PoliticalEvent] = []
    for block in re.findall(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", text, re.S):
        lines = block.splitlines()
        title = clean_text(_ics_value(lines, "SUMMARY").replace("\\,", ",").replace("\\n", " "))
        starts = _ics_datetime(_ics_value(lines, "DTSTART"))
        if not title or not starts or starts < NOW() - timedelta(days=1):
            continue
        location = clean_text(_ics_value(lines, "LOCATION").replace("\\,", ",").replace("\\n", " "))
        url = _ics_value(lines, "URL") or source["page"]
        output.append(
            PoliticalEvent(
                title=title,
                region=source["region"],
                organizer=source["name"],
                event_type=_event_type(title),
                url=url if url.startswith("https://") else source["page"],
                starts_at=starts,
                ends_at=_ics_datetime(_ics_value(lines, "DTEND")),
                venue=location,
            )
        )
    return output


def fetch_ics_events(source: dict[str, str]) -> SourceResult[PoliticalEvent]:
    body, stale, latency, error = CLIENT.get(source["url"])
    items: list[PoliticalEvent] = []
    if body:
        try:
            items = parse_ics_events(body, source)
        except Exception:
            error = "The calendar feed format could not be read."
    return _result(source["name"], source["page"], items, stale, latency, error)


def fetch_events() -> list[SourceResult[PoliticalEvent]]:
    calls = []
    for source in EVENT_SOURCES:
        if source["kind"] == "tribe":
            calls.append((source["name"], lambda s=source: fetch_tribe_events(s)))
        elif source["kind"] == "ics":
            calls.append((source["name"], lambda s=source: fetch_ics_events(s)))
        else:
            calls.append((source["name"], lambda s=source: fetch_html_events(s)))
    return _parallel(calls)


def dedupe_events(results: Iterable[SourceResult[PoliticalEvent]]) -> list[PoliticalEvent]:
    seen: set[str] = set()
    items: list[PoliticalEvent] = []
    now = NOW()
    for item in [x for result in results for x in result.items]:
        if item.starts_at:
            local_start = item.starts_at.astimezone(CENTRAL)
            is_all_day_today = (
                local_start.date() == now.date()
                and local_start.hour == 0
                and local_start.minute == 0
            )
            is_ongoing = bool(
                item.ends_at and item.ends_at.astimezone(CENTRAL) >= now
            )
            if local_start < now and not is_all_day_today and not is_ongoing:
                continue
        day = item.starts_at.date().isoformat() if item.starts_at else "unknown"
        key = re.sub(r"[^a-z0-9]", "", item.title.lower())[:80] + day
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    items.sort(key=lambda x: x.starts_at.timestamp() if x.starts_at else float("inf"))
    return items


def _parallel(calls: list[tuple[str, Callable[[], T]]]) -> list[T]:
    results: list[T] = []
    with ThreadPoolExecutor(max_workers=min(6, len(calls))) as executor:
        futures = {executor.submit(call): name for name, call in calls}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                # Adapter-level failures should not take down unrelated sources.
                continue
    return results


def government_record_priority(
    item: GovernmentRecord, now: datetime | None = None
) -> int:
    """Rank an official record by urgency, recency, and policy significance."""
    reference = now or NOW()
    category_score = {
        "Open meeting": 42,
        "Regulatory docket": 36,
        "Proposed Rules": 34,
        "Adopted Rules": 28,
        "Review Of Agency Rules": 26,
        "Court order or opinion": 26,
        "Court orders": 24,
        "Direct campaign expenditure": 24,
        "Budget and fiscal update": 22,
        "Appointment": 22,
        "Proclamation": 20,
        "Legislative Statement": 24,
        "Press Release": 16,
        "Lobby activity report": 17,
        "State contract": 16,
        "Election and turnout data": 15,
        "Legislative votes and amendments": 14,
        "Lobby registration": 8,
    }.get(item.category, 18)
    score = category_score
    status = item.status.lower()
    if "emergency" in status or "emergency" in item.summary.lower():
        score += 28
    if "proposed" in status:
        score += 18
    elif "adopted" in status or "issued" in status:
        score += 12
    elif "filed" in status:
        score += 8

    if item.occurs_at:
        hours = (item.occurs_at.astimezone(CENTRAL) - reference).total_seconds() / 3600
        if 0 <= hours <= 24:
            score += 55
        elif hours <= 72 and hours >= 0:
            score += 42
        elif hours <= 168 and hours >= 0:
            score += 30
        elif hours <= 720 and hours >= 0:
            score += 14
        elif hours < 0:
            score -= 30
    if item.published_at:
        age_hours = (
            reference - item.published_at.astimezone(CENTRAL)
        ).total_seconds() / 3600
        if 0 <= age_hours <= 24:
            score += 24
        elif age_hours <= 168 and age_hours >= 0:
            score += 15
        elif age_hours <= 720 and age_hours >= 0:
            score += 7
    if item.identifier:
        score += 2
    if item.value:
        score += 3
    return score


def select_action_records(
    records: Iterable[GovernmentRecord],
    now: datetime | None = None,
    limit: int = 8,
    keywords: Iterable[str] = (),
) -> list[GovernmentRecord]:
    """Return a diverse, deduplicated queue of records that merit review."""
    reference = now or NOW()
    terms = tuple(term.strip().lower() for term in keywords if term.strip())
    ranked: list[tuple[int, GovernmentRecord]] = []
    seen: set[str] = set()
    for item in records:
        text = (
            f"{item.title} {item.summary} {item.agency} {item.category} "
            f"{item.status} {item.identifier} {item.value}"
        ).lower()
        if terms and not any(term in text for term in terms):
            continue
        if (
            item.category == "Open meeting"
            and item.occurs_at
            and item.occurs_at.astimezone(CENTRAL) < reference
        ):
            continue
        key = f"{item.url}|{item.identifier or item.title}".lower()
        if key in seen:
            continue
        seen.add(key)
        ranked.append((government_record_priority(item, reference), item))
    ranked.sort(
        key=lambda pair: (
            -pair[0],
            pair[1].occurs_at.timestamp() if pair[1].occurs_at else float("inf"),
            -(pair[1].published_at.timestamp() if pair[1].published_at else 0),
            pair[1].title.lower(),
        )
    )

    selected: list[GovernmentRecord] = []
    overflow: list[GovernmentRecord] = []
    category_counts: dict[str, int] = {}
    category_cap = 2 if terms else 1
    for _, item in ranked:
        if category_counts.get(item.category, 0) >= category_cap:
            overflow.append(item)
            continue
        selected.append(item)
        category_counts[item.category] = category_counts.get(item.category, 0) + 1
        if len(selected) >= limit:
            return selected
    for item in overflow:
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def make_ics(
    events: Iterable[PoliticalEvent | Hearing | GovernmentRecord],
    calendar_name: str,
) -> str:
    def fmt(value: datetime | None) -> str:
        chosen = value or NOW()
        return chosen.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Lone Star Ledger//Texas Political Intelligence//EN",
        f"X-WR-CALNAME:{_ics_escape(calendar_name)}",
    ]
    for item in events:
        title = item.title
        start = getattr(item, "starts_at", None) or getattr(item, "occurs_at", None)
        if not start:
            continue
        end = getattr(item, "ends_at", None) or (start + timedelta(hours=1) if start else None)
        url = item.url
        location = " ".join(
            x
            for x in [
                getattr(item, "venue", ""),
                getattr(item, "address", ""),
                getattr(item, "location", ""),
                getattr(item, "agency", ""),
            ]
            if x
        )
        uid = hashlib.sha1(f"{title}|{start}|{url}".encode()).hexdigest()
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}@lonestarledger",
                f"DTSTAMP:{fmt(NOW())}",
                f"DTSTART:{fmt(start)}",
                f"DTEND:{fmt(end)}",
                f"SUMMARY:{_ics_escape(title)}",
                f"LOCATION:{_ics_escape(location)}",
                f"URL:{url}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def _ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _brief_when(value: datetime) -> str:
    return value.astimezone(CENTRAL).strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def make_briefing(
    today: date,
    hearings: Iterable[Hearing],
    headlines: Iterable[Headline],
    events: Iterable[PoliticalEvent],
    milestones: Iterable[Milestone] = (),
    government_records: Iterable[GovernmentRecord] = (),
    influence_records: Iterable[GovernmentRecord] = (),
    watch_terms: Iterable[str] = (),
) -> str:
    """A shareable Markdown daily brief compiled from already-fetched records."""
    normalized_watch_terms = tuple(watch_terms)
    lines = [
        f"# Texas political intelligence brief — {today.strftime('%B %d, %Y')}",
        "",
        "Prepared by Lone Star Ledger from official and attributed public sources.",
    ]

    upcoming_milestones = sorted(
        (item for item in milestones if item.occurs_on >= today),
        key=lambda item: item.occurs_on,
    )
    if upcoming_milestones:
        lines += ["", "## Key dates"]
        for item in upcoming_milestones[:6]:
            days = (item.occurs_on - today).days
            when = "today" if days == 0 else f"in {days} day{'' if days == 1 else 's'}"
            lines.append(
                f"- {item.name} — {item.occurs_on.strftime('%b %d, %Y')} ({when})"
            )

    lines += ["", "## Committee hearings (next 7 days)"]
    week_end = today + timedelta(days=6)
    week_hearings = sorted(
        (
            item
            for item in hearings
            if item.starts_at
            and today <= item.starts_at.astimezone(CENTRAL).date() <= week_end
        ),
        key=lambda item: item.starts_at,
    )
    if week_hearings:
        for item in week_hearings[:12]:
            location = f" · {item.location}" if item.location else ""
            lines.append(
                f"- {_brief_when(item.starts_at)} · {item.chamber} {item.committee}"
                f"{location} ([notice]({item.url}))"
            )
    else:
        lines.append(
            "- No House or Senate committee meetings are posted for the next seven days."
        )

    reference = datetime.combine(today, datetime.min.time(), tzinfo=CENTRAL) + timedelta(
        hours=8
    )
    official_actions = select_action_records(
        government_records,
        now=reference,
        limit=10,
        keywords=normalized_watch_terms,
    )
    lines += ["", "## Official action queue"]
    if official_actions:
        for item in official_actions:
            if item.occurs_at:
                timing = _brief_when(item.occurs_at)
            elif item.published_at:
                timing = item.published_at.astimezone(CENTRAL).strftime("%b %d")
            else:
                timing = "current"
            identifier = f" · {item.identifier}" if item.identifier else ""
            lines.append(
                f"- [{item.title}]({item.url}) — {item.agency} · {timing}"
                f" · {item.status or item.category}{identifier}"
            )
    else:
        suffix = " matching the watchlist" if normalized_watch_terms else ""
        lines.append(f"- No current official actions were returned{suffix}.")

    lines += ["", "## Top headlines"]
    top_headlines = list(headlines)[:10]
    if top_headlines:
        for item in top_headlines:
            when = (
                item.published_at.astimezone(CENTRAL).strftime("%b %d")
                if item.published_at
                else "recent"
            )
            lines.append(f"- [{item.title}]({item.url}) — {item.publisher}, {when}")
    else:
        lines.append("- No fresh attributed reporting was returned.")

    lines += ["", "## Republican field events (next 14 days)"]
    horizon = today + timedelta(days=13)
    upcoming_events = [
        item
        for item in events
        if item.starts_at and today <= item.starts_at.astimezone(CENTRAL).date() <= horizon
    ]
    if upcoming_events:
        for item in upcoming_events[:12]:
            venue = f" · {item.venue}" if item.venue else ""
            lines.append(
                f"- {_brief_when(item.starts_at)} · {item.title} — {item.organizer}"
                f"{venue} ({item.region})"
            )
    else:
        lines.append("- No dated Republican field events fall inside the next two weeks.")

    influence_actions = select_action_records(
        influence_records,
        now=reference,
        limit=8,
        keywords=normalized_watch_terms,
    )
    lines += ["", "## Influence, spending, and contracts"]
    if influence_actions:
        for item in influence_actions:
            timing = (
                item.published_at.astimezone(CENTRAL).strftime("%b %d")
                if item.published_at
                else "current"
            )
            value = f" · {item.value}" if item.value else ""
            lines.append(
                f"- [{item.title}]({item.url}) — {item.category} · {timing}{value}"
            )
    else:
        suffix = " matching the watchlist" if normalized_watch_terms else ""
        lines.append(f"- No current disclosure or contract records were returned{suffix}.")
    return "\n".join(lines) + "\n"
