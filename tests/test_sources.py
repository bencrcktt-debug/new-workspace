from __future__ import annotations

import io
import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from data_sources import (
    _feed_entries,
    dedupe_events,
    dedupe_headlines,
    fetch_hearing_feed,
    fetch_public_legislator_posts,
    fetch_social_posts,
    fetch_x_list_posts,
    make_ics,
    parse_bullpen_daily,
    parse_finance_workbook,
    parse_ics_events,
    parse_lrl_directory,
    parse_syndication_timeline,
    parse_texan_headlines,
    safe_datetime,
)
from models import Headline, LegislatorSocialAccount, PoliticalEvent, milestone_status


FIXTURES = Path(__file__).parent / "fixtures"
CENTRAL = ZoneInfo("America/Chicago")


def test_rss_parser_cleans_markup_and_parses_time() -> None:
    entries = _feed_entries((FIXTURES / "feed.xml").read_bytes())
    assert len(entries) == 1
    assert entries[0]["title"] == "HB 1 - Filed"
    assert entries[0]["summary"] == "Relating to property tax relief."
    assert entries[0]["published"].tzinfo is not None


def test_rss_parser_rejects_malformed_xml() -> None:
    with pytest.raises(Exception):
        _feed_entries(b"<rss><broken>")


def test_lrl_directory_normalizes_chambers_and_handles() -> None:
    accounts = parse_lrl_directory((FIXTURES / "lrl_directory.html").read_bytes())
    assert [(a.name, a.chamber, a.handle) for a in accounts] == [
        ("Jane Example", "House", "JaneTX"),
        ("John Example", "Senate", "JohnTX"),
    ]


def test_finance_workbook_requires_expected_columns() -> None:
    frame = pd.DataFrame(
        [
            {
                "#": None,
                "Unnamed: 1": 1,
                "Contributions Received": 1000,
                "Political Expenditures": 250,
                "Filer Type": "GPAC",
                "Filer Name": "Example PAC",
                "Filer ID": "42",
                "City": "Austin",
                "State": "TX",
            }
        ]
    )
    output = io.BytesIO()
    frame.to_excel(output, index=False)
    records = parse_finance_workbook(output.getvalue(), "https://example.com/data.xlsx")
    assert records[0].filer_name == "Example PAC"
    assert records[0].net_activity == 750


def test_finance_workbook_detects_schema_drift() -> None:
    output = io.BytesIO()
    pd.DataFrame([{"unexpected": 1}]).to_excel(output, index=False)
    with pytest.raises(ValueError, match="Unexpected"):
        parse_finance_workbook(output.getvalue(), "https://example.com/data.xlsx")


def test_datetime_is_normalized_to_central() -> None:
    parsed = safe_datetime("2026-07-23T16:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo == CENTRAL
    assert parsed.hour == 11


def test_hearing_date_and_time_are_parsed_from_tlo_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b"""<?xml version="1.0"?><rss><channel><item>
    <title>State Affairs - 8/17/2026</title>
    <description>Time: 10:30 AM, Location: E1.026</description>
    <link>https://capitol.texas.gov/notice</link></item></channel></rss>"""

    monkeypatch.setattr(
        "data_sources.CLIENT.get",
        lambda *_args, **_kwargs: (body, False, 12, ""),
    )
    result = fetch_hearing_feed("House", "https://capitol.texas.gov/feed")
    assert result.items[0].committee == "State Affairs"
    assert result.items[0].starts_at == datetime(2026, 8, 17, 10, 30, tzinfo=CENTRAL)
    assert result.items[0].location == "E1.026"


def test_x_lookup_is_bounded_to_ten_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[str] = []

    def fake_get(url: str, headers: dict | None = None):
        requested.append(url)
        return json.dumps({"data": []}).encode(), False, 5, ""

    monkeypatch.setattr("data_sources.CLIENT.get", fake_get)
    accounts = [
        LegislatorSocialAccount(f"Member {i}", "House", f"handle{i}", f"https://x.com/handle{i}")
        for i in range(12)
    ]
    fetch_social_posts("token", "https://api.x.com", accounts)
    usernames = parse_qs(urlparse(requested[0]).query)["usernames"][0].split(",")
    assert len(usernames) == 10
    assert "handle10" not in usernames


def test_x_list_posts_are_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "data": [
            {
                "id": "123",
                "author_id": "42",
                "text": "Texas legislative update",
                "created_at": "2026-07-23T18:00:00Z",
                "public_metrics": {"like_count": 8, "retweet_count": 3},
            }
        ],
        "includes": {"users": [{"id": "42", "name": "Jane Example", "username": "JaneTX"}]},
    }
    monkeypatch.setattr(
        "data_sources.CLIENT.get",
        lambda *_args, **_kwargs: (json.dumps(payload).encode(), False, 9, ""),
    )
    result = fetch_x_list_posts("token", "https://api.x.com")
    assert result.freshness == "live"
    assert result.items[0].handle == "JaneTX"
    assert result.items[0].url == "https://x.com/JaneTX/status/123"


def test_syndication_timeline_keeps_only_original_posts() -> None:
    account = LegislatorSocialAccount("Jane Example", "House", "JaneTX", "https://x.com/JaneTX")
    posts = parse_syndication_timeline(
        (FIXTURES / "x_syndication.html").read_bytes(), account, limit=5
    )
    # Reply (1002) and retweet (1003) are dropped; two original posts remain, newest first.
    assert [p.url for p in posts] == [
        "https://x.com/JaneTX/status/1001",
        "https://x.com/JaneTX/status/1004",
    ]
    assert posts[0].text == "Filed a bill on property tax relief today. & more to come."
    assert posts[0].legislator_name == "Jane Example"
    assert posts[0].likes == 42 and posts[0].reposts == 7
    assert posts[0].created_at == datetime(2026, 7, 22, 10, 30, tzinfo=CENTRAL)


def test_public_feed_reports_unavailable_without_accounts() -> None:
    result = fetch_public_legislator_posts([])
    assert result.freshness == "unavailable"
    assert not result.items


def test_public_feed_merges_and_sorts_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = (FIXTURES / "x_syndication.html").read_bytes()
    monkeypatch.setattr(
        "data_sources.SYNDICATION_CLIENT.get",
        lambda *_args, **_kwargs: (fixture, False, 7, ""),
    )
    accounts = [
        LegislatorSocialAccount(f"Member {i}", "House", f"handle{i}", f"https://x.com/handle{i}")
        for i in range(3)
    ]
    result = fetch_public_legislator_posts(accounts, per_account=2, total=10)
    assert result.freshness == "live"
    # 3 accounts x 2 original posts each, newest first across the merged set.
    assert len(result.items) == 6
    timestamps = [p.created_at for p in result.items]
    assert timestamps == sorted(timestamps, reverse=True)


def test_texan_homepage_parser_uses_article_timestamp() -> None:
    body = b"""<html><body><article>
    <a href="/state/article_123.html" aria-label="Fresh Texas policy story"></a>
    <time datetime="2026-07-23T14:57:00-05:00">now</time>
    <p class="summary">A Texas Legislature update.</p>
    </article></body></html>"""
    items = parse_texan_headlines(body)
    assert items[0].publisher == "The Texan"
    assert items[0].published_at == datetime(2026, 7, 23, 14, 57, tzinfo=CENTRAL)


def test_bullpen_daily_parser_uses_newsletter_date() -> None:
    body = b"""<html><body><div class="e-con-inner"><h6>
    <a href="/daily-bull/texas-policy-update/">Texas Policy Update, Border News, and the Legislature</a>
    </h6><p>By Texas Bullpen July 23, 2026</p></div></body></html>"""
    items = parse_bullpen_daily(body)
    assert items[0].publisher == "Texas Bullpen"
    assert items[0].published_at == datetime(2026, 7, 23, 0, 0, tzinfo=CENTRAL)


def test_headlines_are_deduplicated_and_ranked() -> None:
    now = datetime(2026, 7, 23, 12, tzinfo=CENTRAL)
    first = Headline("Texas Legislature acts", "A", "https://a.example", now, relevance=4)
    duplicate = Headline("Texas Legislature acts!", "B", "https://b.example", now, relevance=2)
    other = Headline("County update", "C", "https://c.example", now, relevance=1)
    from models import SourceResult

    result = SourceResult("news", "https://example.com", [other, duplicate, first])
    deduped = dedupe_headlines([result])
    assert len(deduped) == 2
    assert deduped[0].relevance == 4


def test_events_dedupe_and_ics_escape() -> None:
    starts = datetime(2026, 8, 1, 18, tzinfo=CENTRAL)
    event = PoliticalEvent(
        "Republican Club Meeting",
        "Austin",
        "County GOP",
        "Club meeting",
        "https://example.com/event",
        starts,
        venue="Hall, Room A",
    )
    from models import SourceResult

    result = SourceResult("events", "https://example.com", [event, event])
    assert len(dedupe_events([result])) == 1
    calendar = make_ics([event], "Texas GOP")
    assert "BEGIN:VEVENT" in calendar
    assert "Hall\\, Room A" in calendar


def test_public_ics_event_parser_normalizes_utc() -> None:
    body = b"""BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260818T233000Z
DTEND:20260819T010000Z
SUMMARY:County Executive Committee Meeting
LOCATION:Community Center\\, Room A
URL:https://example.com/meeting
END:VEVENT
END:VCALENDAR"""
    source = {
        "name": "County GOP",
        "region": "Houston",
        "page": "https://example.com/calendar",
    }
    items = parse_ics_events(body, source)
    assert items[0].starts_at == datetime(2026, 8, 18, 18, 30, tzinfo=CENTRAL)
    assert items[0].venue == "Community Center, Room A"


@pytest.mark.parametrize(
    ("target", "today", "label"),
    [
        (date(2026, 11, 3), date(2026, 11, 2), "1 day"),
        (date(2026, 11, 3), date(2026, 11, 3), "Today"),
        (date(2026, 11, 3), date(2026, 11, 4), "Complete"),
    ],
)
def test_milestone_status(target: date, today: date, label: str) -> None:
    assert milestone_status(target, today)[0] == label
