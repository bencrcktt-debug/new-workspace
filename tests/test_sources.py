from __future__ import annotations

import io
import json
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import requests

from data_sources import (
    TLO_FEEDS,
    TRIBUNE_FEED,
    SyndicationClient,
    _feed_entries,
    dedupe_events,
    dedupe_headlines,
    extract_topics,
    fetch_hearing_feed,
    fetch_html_events,
    fetch_public_legislator_posts,
    fetch_headline_feed,
    fetch_social_posts,
    fetch_x_list_posts,
    government_record_priority,
    headline_relevance,
    headline_priority,
    is_texas_political_story,
    make_briefing,
    make_ics,
    matched_watch_terms,
    parse_watch_terms,
    parse_bullpen_daily,
    parse_budget_updates,
    parse_contracts,
    parse_court_order_page,
    parse_direct_expenditures,
    parse_election_data_sources,
    parse_finance_workbook,
    parse_governor_actions,
    parse_ics_events,
    parse_lobby_roster,
    parse_lobby_activity,
    parse_lrl_directory,
    parse_open_meetings,
    parse_indexed_x_posts,
    parse_syndication_timeline,
    parse_texas_register,
    parse_texan_headlines,
    safe_datetime,
    select_action_records,
)
from models import (
    GovernmentRecord,
    Headline,
    Hearing,
    LegislatorSocialAccount,
    Milestone,
    PoliticalEvent,
    milestone_status,
    next_milestones,
)


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


def test_watch_terms_are_deduplicated_and_phrase_matches_are_explicit() -> None:
    terms = parse_watch_terms(" ERCOT, water, ercot,  ")
    assert terms == ("ERCOT", "water")
    assert matched_watch_terms("ERCOT water-market discussion", terms) == ("ERCOT", "water")


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


def _fake_response(status: int, body: bytes = b"", headers: dict | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.url = "https://syndication.twitter.com/test"
    if headers:
        response.headers.update(headers)
    return response


def test_syndication_client_gates_after_429_and_serves_last_good(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = SyndicationClient(cache_dir=str(tmp_path))
    reset_at = str(time.time() + 600)
    responses = [
        _fake_response(200, b"payload"),
        _fake_response(429, b"Rate limit exceeded", {"x-rate-limit-reset": reset_at}),
    ]
    calls: list[str] = []

    def fake_get(url: str, headers=None, timeout=None):
        calls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(client.session, "get", fake_get)
    url = "https://syndication.twitter.com/srv/timeline-profile/screen-name/JaneTX"

    body, stale, _, error = client.get(url)
    assert (body, stale, error) == (b"payload", False, "")

    body, stale, _, error = client.get(url)  # the 429 opens the gate
    assert body == b"payload" and stale
    assert "rate-limited" in error

    body, stale, _, error = client.get(url)  # gated: served without a network call
    assert body == b"payload" and stale
    assert "quota" in error
    assert len(calls) == 2
    assert client.blocked_seconds() > 0


def test_syndication_disk_cache_survives_a_fresh_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = "https://syndication.twitter.com/srv/timeline-profile/screen-name/JaneTX"
    first = SyndicationClient(cache_dir=str(tmp_path))
    monkeypatch.setattr(
        first.session, "get", lambda *_a, **_k: _fake_response(200, b"payload")
    )
    assert first.get(url)[0] == b"payload"

    fresh = SyndicationClient(cache_dir=str(tmp_path))  # e.g. after a restart
    monkeypatch.setattr(
        fresh.session,
        "get",
        lambda *_a, **_k: _fake_response(429, b"", {"x-rate-limit-reset": ""}),
    )
    body, stale, _, error = fresh.get(url)
    assert body == b"payload" and stale
    assert "last successful response" in error


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


def test_public_feed_broadens_index_coverage_without_more_x_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct: list[str] = []
    indexed: list[str] = []

    def fake_direct(account: LegislatorSocialAccount, _limit: int):
        direct.append(account.handle)
        return [], False, 1

    def fake_indexed(account: LegislatorSocialAccount, _limit: int):
        indexed.append(account.handle)
        return [], False, 1

    monkeypatch.setattr("data_sources._syndication_account_posts", fake_direct)
    monkeypatch.setattr("data_sources._indexed_account_posts", fake_indexed)
    accounts = [
        LegislatorSocialAccount(f"Member {i}", "House", f"handle{i}", f"https://x.com/handle{i}")
        for i in range(24)
    ]

    fetch_public_legislator_posts(accounts)

    assert len(direct) == 6
    assert len(indexed) == 18


def test_documented_primary_feeds_are_configured() -> None:
    assert TRIBUNE_FEED == "https://feeds.texastribune.org/feeds/main/"
    assert "House calendars" in TLO_FEEDS
    assert "Senate calendars" in TLO_FEEDS


def test_indexed_x_feed_provides_recent_no_token_posts() -> None:
    account = LegislatorSocialAccount(
        "Dustin Burrows", "House", "Burrows4TX", "https://x.com/Burrows4TX"
    )
    body = b"""<?xml version="1.0"?><rss><channel><item>
    <title>Texas House update from the Speaker - x.com</title>
    <link>https://news.google.com/rss/articles/example</link>
    <pubDate>Tue, 21 Jul 2026 19:51:26 GMT</pubDate>
    </item></channel></rss>"""
    posts = parse_indexed_x_posts(body, account)
    assert len(posts) == 1
    assert posts[0].handle == "Burrows4TX"
    assert posts[0].text == "Texas House update from the Speaker"
    assert posts[0].created_at == datetime(2026, 7, 21, 14, 51, 26, tzinfo=CENTRAL)


def test_news_relevance_requires_texas_and_political_context() -> None:
    assert is_texas_political_story("Texas Senate committee schedules a property tax hearing")
    assert not is_texas_political_story("Senate Republicans debate a federal spending bill")
    assert not is_texas_political_story("Texas A&M Forest Service adds wildfire aircraft")
    assert headline_relevance(
        "Texas House committee schedules a property tax hearing",
        publisher="The Texas Tribune",
    ) > headline_relevance("National politics roundup")


def test_news_priority_balances_recency_and_relevance() -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=CENTRAL)
    fresh = Headline(
        "Texas agency issues an order",
        "Local",
        "https://example.com/fresh",
        now,
        relevance=10,
    )
    old = Headline(
        "Texas Legislature committee hearing",
        "Local",
        "https://example.com/old",
        datetime(2026, 7, 20, 12, tzinfo=CENTRAL),
        relevance=17,
    )
    assert headline_priority(fresh, now) > headline_priority(old, now)


def test_reachable_empty_feed_is_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "data_sources.CLIENT.get",
        lambda *_args, **_kwargs: (
            b"<?xml version='1.0'?><rss><channel></channel></rss>",
            False,
            5,
            "",
        ),
    )
    result = fetch_headline_feed("Empty but reachable", "https://example.com/feed")
    assert result.freshness == "live"
    assert result.items == []
    assert "no current records" in result.error


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


def test_syndicated_rewordings_are_deduplicated() -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=CENTRAL)
    first = Headline(
        "Ken Paxton has long crusaded against voter fraud. His Senate rival now accuses him of committing it.",
        "The Texas Tribune",
        "https://example.com/tribune",
        now,
        relevance=13,
    )
    syndicated = Headline(
        "Ken Paxton Touts His Efforts to Fight Voter Fraud. His Senate Opponent Is Now Accusing Him of Committing It.",
        "ProPublica",
        "https://example.com/propublica",
        now,
        relevance=9,
    )
    from models import SourceResult

    result = SourceResult("news", "https://example.com", [syndicated, first])
    deduped = dedupe_headlines([result])
    assert len(deduped) == 1
    assert deduped[0].publisher == "The Texas Tribune"


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
    meeting = GovernmentRecord(
        title="Agency hearing",
        category="Open meeting",
        agency="Public Utility Commission",
        url="https://example.com/agency-hearing",
        occurs_at=starts,
    )
    government_calendar = make_ics([meeting], "Texas government")
    assert "SUMMARY:Agency hearing" in government_calendar
    assert "LOCATION:Public Utility Commission" in government_calendar


def test_calendar_excludes_events_that_already_ended_their_start_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "data_sources.NOW",
        lambda: datetime(2026, 8, 1, 15, tzinfo=CENTRAL),
    )
    past = PoliticalEvent(
        "Past luncheon",
        "Austin",
        "County GOP",
        "Club meeting",
        "https://example.com/past",
        datetime(2026, 8, 1, 12, tzinfo=CENTRAL),
    )
    upcoming = PoliticalEvent(
        "Evening reception",
        "Austin",
        "County GOP",
        "Fundraiser",
        "https://example.com/upcoming",
        datetime(2026, 8, 1, 18, tzinfo=CENTRAL),
    )
    from models import SourceResult

    assert dedupe_events([SourceResult("events", "https://example.com", [past, upcoming])]) == [
        upcoming
    ]


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


def test_simple_calendar_html_events_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b"""<html><body><li class="simcal-event">
    <span class="simcal-event-title">County Candidate Forum</span>
    <span itemprop="startDate" content="2026-08-18T18:30:00-05:00">August 18</span>
    <span itemprop="endDate" content="2026-08-18T20:00:00-05:00">8 pm</span>
    <span class="simcal-event-address">Community Hall, San Antonio</span>
    <a href="https://example.com/forum">Details</a>
    </li></body></html>"""
    monkeypatch.setattr(
        "data_sources.CLIENT.get",
        lambda *_args, **_kwargs: (body, False, 7, ""),
    )
    source = {
        "name": "County GOP",
        "region": "San Antonio",
        "url": "https://example.com/calendar",
        "page": "https://example.com/calendar",
    }
    result = fetch_html_events(source)
    assert result.freshness == "live"
    assert result.items[0].title == "County Candidate Forum"
    assert result.items[0].starts_at == datetime(2026, 8, 18, 18, 30, tzinfo=CENTRAL)
    assert result.items[0].ends_at == datetime(2026, 8, 18, 20, 0, tzinfo=CENTRAL)
    assert result.items[0].venue == "Community Hall, San Antonio"


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


def test_next_milestones_skips_past_and_sorts() -> None:
    milestones = [
        Milestone("Later", date(2027, 1, 12), "Legislature", "https://example.com"),
        Milestone("Past", date(2026, 7, 15), "Campaign finance", "https://example.com"),
        Milestone("Sooner", date(2026, 11, 3), "Election", "https://example.com"),
    ]
    upcoming = next_milestones(milestones, date(2026, 7, 24), count=2)
    assert [m.name for m in upcoming] == ["Sooner", "Later"]


def test_extract_topics_labels_in_fixed_order_without_duplicates() -> None:
    topics = extract_topics(
        "Texas Legislature weighs property tax cut as txlege campaign season begins"
    )
    assert topics == ["Legislature", "Elections", "Property tax"]
    assert extract_topics("City council approves zoning update") == []


def test_briefing_compiles_all_sections() -> None:
    today = date(2026, 7, 24)
    hearing = Hearing(
        title="State Affairs - 7/28/2026",
        chamber="House",
        committee="State Affairs",
        location="E1.026",
        url="https://capitol.texas.gov/notice",
        starts_at=datetime(2026, 7, 28, 10, 30, tzinfo=CENTRAL),
    )
    headline = Headline(
        "Texas Legislature acts",
        "The Texas Tribune",
        "https://example.com/story",
        datetime(2026, 7, 23, 9, tzinfo=CENTRAL),
    )
    event = PoliticalEvent(
        "County Club Meeting",
        "Austin",
        "Travis County GOP",
        "Club meeting",
        "https://example.com/event",
        datetime(2026, 7, 30, 18, 30, tzinfo=CENTRAL),
        venue="Community Hall",
    )
    meeting = GovernmentRecord(
        title="Public Utility Commission open meeting",
        category="Open meeting",
        agency="Public Utility Commission of Texas",
        url="https://example.com/meeting",
        occurs_at=datetime(2026, 7, 25, 9, tzinfo=CENTRAL),
        status="Accepted",
        identifier="TRD-2026001",
    )
    expenditure = GovernmentRecord(
        title="Texans for Reliable Energy",
        category="Direct campaign expenditure",
        agency="Texas Ethics Commission",
        url="https://example.com/expenditure",
        published_at=datetime(2026, 7, 24, 7, tzinfo=CENTRAL),
        status="Filed",
        value="$125,000.00",
    )
    milestone = Milestone("Texas general election", date(2026, 11, 3), "Election", "https://example.com")
    brief = make_briefing(
        today,
        [hearing],
        [headline],
        [event],
        [milestone],
        [meeting],
        [expenditure],
    )
    assert "# Texas political intelligence brief — July 24, 2026" in brief
    assert "Texas general election — Nov 03, 2026 (in 102 days)" in brief
    assert "House State Affairs · E1.026 ([notice](https://capitol.texas.gov/notice))" in brief
    assert "[Texas Legislature acts](https://example.com/story) — The Texas Tribune, Jul 23" in brief
    assert "County Club Meeting — Travis County GOP · Community Hall (Austin)" in brief
    assert "## Official action queue" in brief
    assert "Public Utility Commission open meeting" in brief
    assert "## Influence, spending, and contracts" in brief
    assert "Texans for Reliable Energy" in brief


def test_briefing_states_when_sections_are_empty() -> None:
    brief = make_briefing(date(2026, 7, 24), [], [], [])
    assert "No House or Senate committee meetings are posted" in brief
    assert "No fresh attributed reporting was returned." in brief
    assert "No dated Republican field events fall inside the next two weeks." in brief
    assert "No current official actions were returned." in brief
    assert "No current disclosure or contract records were returned." in brief


def test_action_queue_prioritizes_urgent_records_and_stays_diverse() -> None:
    now = datetime(2026, 7, 24, 8, tzinfo=CENTRAL)
    records = [
        GovernmentRecord(
            title="Emergency grid reliability meeting",
            category="Open meeting",
            agency="Public Utility Commission",
            url="https://example.com/urgent",
            occurs_at=datetime(2026, 7, 24, 12, tzinfo=CENTRAL),
            summary="Emergency meeting",
            status="Accepted",
        ),
        GovernmentRecord(
            title="Routine meeting next month",
            category="Open meeting",
            agency="Routine Agency",
            url="https://example.com/routine",
            occurs_at=datetime(2026, 8, 20, 12, tzinfo=CENTRAL),
            status="Accepted",
        ),
        GovernmentRecord(
            title="Water quality proposed rule",
            category="Proposed Rules",
            agency="TCEQ",
            url="https://example.com/rule",
            published_at=datetime(2026, 7, 23, 10, tzinfo=CENTRAL),
            status="Proposed",
        ),
        GovernmentRecord(
            title="Water infrastructure contract",
            category="State contract",
            agency="Texas Comptroller",
            url="https://example.com/contract",
            value="$4,000,000",
            status="Active listing",
        ),
    ]
    selected = select_action_records(records, now=now, limit=3)
    assert selected[0].title == "Emergency grid reliability meeting"
    assert {item.category for item in selected} == {
        "Open meeting",
        "Proposed Rules",
        "State contract",
    }
    water = select_action_records(records, now=now, keywords=["water"])
    assert {item.title for item in water} == {
        "Water quality proposed rule",
        "Water infrastructure contract",
    }
    assert government_record_priority(records[0], now) > government_record_priority(
        records[1], now
    )


def test_open_meetings_parser_normalizes_pending_notice() -> None:
    body = b"""<html><body><pre>
Status: Accepted
TRD: 2026009999
Related TRD: N/A
Submitted Date/Time: 7/24/2026 9:30 AM CDT
Agency Name: Public Utility Commission of Texas
Board: Commission Open Meeting
Committee: N/A
Meeting Date: 7/30/2026
Meeting Time: 09:30 AM (Local Time)
Address: 1701 N Congress Ave
City: Austin
State: TX
Additional Information: N/A
Emergency Meeting: No
Emergency Reason: N/A
Agenda: Consideration of posted electric market matters.
==============================================================================
</pre></body></html>"""
    records = parse_open_meetings(body)
    assert len(records) == 1
    assert records[0].identifier == "2026009999"
    assert records[0].agency == "Public Utility Commission of Texas"
    assert records[0].occurs_at == datetime(2026, 7, 30, 9, 30, tzinfo=CENTRAL)


def test_texas_register_parser_tracks_rule_status_and_agency() -> None:
    body = b"""<html><body>
    <h1>Texas Register July 24, 2026 Volume 51 Number 30</h1>
    <h3>PROPOSED RULES</h3>
    <p><a href="../agency.html">PUBLIC UTILITY COMMISSION OF TEXAS</a></p>
    <blockquote><a href="../rule.html#1">16 TAC &#167;25.192</a></blockquote>
    <h3>ADOPTED RULES</h3>
    <p><a href="../tea.html">TEXAS EDUCATION AGENCY</a></p>
    <blockquote><a href="../tea-rule.html#2">19 TAC &#167;74.11</a></blockquote>
    </body></html>"""
    records = parse_texas_register(body)
    assert [(item.agency, item.status) for item in records] == [
        ("PUBLIC UTILITY COMMISSION OF TEXAS", "Proposed"),
        ("TEXAS EDUCATION AGENCY", "Adopted"),
    ]


def test_direct_expenditure_parser_reads_current_tec_rows() -> None:
    body = b"""<table>
    <tr><th>Rank</th><th>IDs</th><th>Filer</th><th>Unitemized</th><th>Itemized</th><th>Date</th></tr>
    <tr><td>1</td><td>101060602 00091092</td><td>Building Tomorrow Together</td>
    <td>$10.00</td><td>$112,568.35</td><td>07/15/2026</td></tr>
    </table>"""
    records = parse_direct_expenditures(body)
    assert len(records) == 1
    assert records[0].title == "Building Tomorrow Together"
    assert records[0].value == "$112,578.35"


def test_lobby_roster_parser_exposes_client_lobbyist_and_disclosed_range() -> None:
    frame = pd.DataFrame(
        [
            {
                "Client Name": "Example Energy",
                "FilerID": 12345,
                "Lobby Name": "Doe, Jane",
                "Start": "01/01/2026",
                "Stop": "12/31/2026",
                "Method Payment": "PAID",
                "Amount": "LOBBCOMP03",
            }
        ]
    )
    output = io.BytesIO()
    frame.to_excel(output, index=False)
    records = parse_lobby_roster(output.getvalue())
    assert len(records) == 1
    assert records[0].title == "Example Energy — Doe, Jane"
    assert records[0].value == "$57,220–$114,429.99"


def test_lobby_activity_parser_reads_filed_reports() -> None:
    body = b"""<table class="jrPage">
    <tr><td>00085404</td><td>Abboud, Andy</td></tr>
    <tr><td><a href="http://example.com/report.pdf">101056165</a></td>
    <td>LOBBYACTJUL</td><td>Filed 07/02/2026</td>
    <td>Covering 2026-06-01 thru 2026-06-30</td></tr>
    </table>"""
    records = parse_lobby_activity(body)
    assert len(records) == 1
    assert records[0].title == "Abboud, Andy — LOBBYACTJUL"
    assert records[0].identifier == "00085404 · 101056165"


def test_governor_action_parser_keeps_category_and_date() -> None:
    body = b"""<div class="media-object m-b-4">
    <div>Jul 24</div><div><h3><a href="/news/post/example-action">
    Governor Announces Appointment</a></h3>
    <span>Press Release, Appointment</span></div></div>"""
    records = parse_governor_actions(
        body, datetime(2026, 7, 24, 12, tzinfo=CENTRAL)
    )
    assert len(records) == 1
    assert records[0].category == "Appointment"
    assert records[0].published_at == datetime(2026, 7, 24, tzinfo=CENTRAL)


def test_court_parser_creates_case_level_records() -> None:
    body = b"""<html><body><p>26-0127 EXAMPLE AGENCY v. EXAMPLE COMPANY</p>
    <p>The Court grants the petition for review.</p></body></html>"""
    pronounced = datetime(2026, 7, 24, tzinfo=CENTRAL)
    records = parse_court_order_page(body, "https://example.com/orders", pronounced)
    assert len(records) == 1
    assert records[0].identifier == "26-0127"
    assert records[0].published_at == pronounced


def test_election_index_parser_keeps_current_data_products() -> None:
    body = b"""<html><body>
    <a href="/turnout">Early Voting Turnout (current)</a>
    <a href="/results">Historical Election Results</a>
    </body></html>"""
    records = parse_election_data_sources(body)
    assert len(records) == 2
    assert records[0].agency == "Texas Secretary of State"


def test_contract_parser_normalizes_supplier_and_amount() -> None:
    body = b"""<table><tr><th>Supplier</th><th>PO</th><th>Description</th><th>Amount</th></tr>
    <tr><td>EXAMPLE VENDOR LLC</td><td>26-1000</td><td>Data services</td><td>$12,500.00</td></tr>
    </table>"""
    records = parse_contracts(body)
    assert len(records) == 1
    assert records[0].identifier == "26-1000"
    assert records[0].value == "$12,500.00"


def test_budget_update_parser_reads_lbb_publications() -> None:
    body = b"""<div class="documentslist"><a class="recentDocsLink" href="report.pdf">
    Fiscal Update</a><br><span>July 22, 2026| Policy Report</span></div>"""
    records = parse_budget_updates(body)
    assert len(records) == 1
    assert records[0].agency == "Legislative Budget Board"
    assert records[0].summary == "Policy Report"
