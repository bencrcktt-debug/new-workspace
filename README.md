# Lone Star Ledger

A responsive Texas legislative and Republican political-intelligence dashboard for government-affairs professionals. It runs as a stateless Streamlit application and is compatible with Streamlit Community Cloud.

## What it monitors

- Texas Legislature Online bill activity and House/Senate hearing notices, grouped by hearing day
- Election, campaign-finance, bill-prefiling, session, and sine-die deadlines through the 90th Legislature
- Current Texas Ethics Commission PAC contributions and expenditures, with a top-10 comparison chart and CSV export
- Fresh Texas political headlines from direct publishers and tightly filtered seven-day multi-publisher feeds, with issue tags, priority scoring, recency windows, and near-duplicate removal
- The Legislative Reference Library's current directory of legislators on X
- A command-center Legislator Pulse board of recent legislator posts, pulled from X's public syndication endpoint with no token required (falls back to the official roster when rate-limited)
- Optional token-based, account-filtered X posts (X API v2) for a bounded selection of legislators
- Republican Party and club events across Austin, Dallas–Fort Worth, Houston, San Antonio, and statewide sources, presented as a filterable day-by-day agenda
- Runtime freshness, latency, record counts, and fallback status for every checked source

The command center also assembles a one-click **daily brief** — a Markdown download that compiles upcoming deadlines, this week's hearings with notice links, the top ten headlines, and the next two weeks of Republican field events. Countdown cards select the next three flagship political dates automatically, so the board never shows an expired countdown.

The application identifies official records separately from attributed media, social, and event intelligence.

## Run locally

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Open `http://localhost:8501`.

## Configure X

No X credentials are required. The command center's Legislator Pulse board and the "Legislators on X" feed both read X's public syndication timelines — the same JSON X's own embed widgets use. That endpoint allows about 30 anonymous requests per window per IP, and the app is engineered to live inside that budget:

- Each refresh reads at most six accounts and results are cached for 30 minutes.
- A 429 response closes a gate until X's advertised reset time, so the app never deepens its own rate limit by retrying.
- Every successful timeline is stored on disk, so the last-good posts continue to display through rate-limit windows, cache clears, and app restarts.
- Recently indexed public status pages are merged with X's syndication timelines, which keeps the no-token feed current when X returns only pinned or older posts.
- While no posts have ever been fetched and the gate is closed, the board shows the official LRL legislator roster with an honest note about when the feed resumes.

An X API v2 bearer token is optional and never required: if one is present in secrets or entered in the "Legislators on X" tab, the app switches to the API list timeline (one request covers every legislator) and uses the public feed as its fallback.

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`:

```toml
X_BEARER_TOKEN = "your-token"
X_API_BASE_URL = "https://api.x.com"
SOCIAL_DEFAULT_HANDLES = ["Burrows4TX", "joanhuffman"]
```

`SOCIAL_DEFAULT_HANDLES` must match accounts in the current LRL directory. The older `TXLEGE_X_HANDLES` setting remains accepted as a fallback.

Never commit `.streamlit/secrets.toml`.

## Deploy to Streamlit Community Cloud

1. Push the repository to GitHub.
2. Create a Streamlit Community Cloud app with `app.py` as the entry point.
3. Paste any X settings into the deployment's Secrets editor.
4. Deploy. All non-X sections work without private credentials.

Remote results are cached by source: X for 5 minutes, legislative/news feeds for 15 minutes, events for 30 minutes, the TEC workbook for 6 hours, and the LRL directory for 24 hours.

## Source behavior

| Area | Primary source | Behavior |
|---|---|---|
| Legislation and hearings | Texas Legislature Online RSS | Official records; links return to TLO |
| Campaign finance | Texas Ethics Commission 2026 PAC workbook | In-app totals and top-10 chart; signed reports and cash-on-hand searches remain authoritative |
| Legislator accounts | Legislative Reference Library | Parsed daily; no party affiliation inferred |
| Headlines | Direct Texas publishers and three tightly scoped seven-day Google News RSS queries | Texas-policy filtered, attributed, near-deduplicated, and priority-ranked |
| Republican events | RPT and major-county public calendars, including Williamson and Bexar County | WordPress, HTML, Simple Calendar, and ICS adapters feed a day-grouped agenda |
| X posts (command center) | X public syndication timelines plus indexed public status pages | No token; recent posts are merged from two public paths, bounded, cached, and rate-limit aware |
| X posts (Legislators on X tab) | X API v2 list-posts and user-posts endpoints | Requires post-read access; account-filtered requests are limited to 10 accounts |

If a source fails after a successful request, the in-process last-good response is shown as stale. If no successful response exists, the relevant page remains usable with an unavailable state and authoritative links.

## Add a county event source

Add an entry to `EVENT_SOURCES` in `data_sources.py`:

```python
{
    "name": "Example County GOP",
    "region": "Example Metro",
    "kind": "tribe",  # "tribe" or "html"
    "url": "https://example.org/wp-json/tribe/events/v1/events",
    "page": "https://example.org/events/",
}
```

Use `tribe` for The Events Calendar REST API. Use `html` only for a stable, public event listing that exposes semantic `article`, event, link, and `time` elements.

## Test

```powershell
python -m pytest -q
```

Parser tests use local fixtures. The Streamlit smoke test exercises every application section.

## Project layout

- `app.py` — Streamlit presentation and interaction layer
- `models.py` — normalized records, milestone helpers, and source-result contracts
- `data_sources.py` — resilient connectors, parsers, ranking, deduplication, issue tagging, ICS generation, and the daily-brief builder
- `tests/` — parser fixtures, source-contract tests, and UI smoke tests
