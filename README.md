# Lone Star Ledger

A responsive Texas legislative and Republican political-intelligence dashboard for government-affairs professionals. It runs as a stateless Streamlit application and is compatible with Streamlit Community Cloud.

## What it monitors

- Texas Legislature Online bill activity and House/Senate hearing notices
- Election, campaign-finance, bill-prefiling, and session deadlines
- Current Texas Ethics Commission PAC contributions and expenditures
- Fresh Texas political headlines from the Texas Tribune and three seven-day multi-publisher feeds
- The Legislative Reference Library's current directory of legislators on X
- A live X feed of recent legislator posts on the command center, pulled from X's public syndication endpoint with no token required
- Optional token-based, account-filtered X posts (X API v2) for a bounded selection of legislators
- Republican Party and club events across Austin, Dallas–Fort Worth, Houston, San Antonio, and statewide sources
- Runtime freshness, latency, record counts, and fallback status for every checked source

The application identifies official records separately from attributed media, social, and event intelligence.

## Run locally

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Open `http://localhost:8501`.

## Configure X

The command center's live legislator feed and the complete legislator directory both work without credentials — the feed reads X's public syndication timelines, the same JSON X's own embed widgets use. That endpoint is rate-limited for anonymous callers (about 30 requests per window per IP), so results are cached and the app falls back to the embedded list timeline and a direct link when the limit is hit.

Only the account-filtered cards on the "Legislators on X" tab require X API v2 read access. A token can be entered for the current browser session or stored in Streamlit secrets.

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
| Campaign finance | Texas Ethics Commission 2026 PAC workbook | In-app totals; signed reports and cash-on-hand searches remain authoritative |
| Legislator accounts | Legislative Reference Library | Parsed daily; no party affiliation inferred |
| Headlines | Texas Tribune politics feed and three seven-day Google News RSS queries | Deduplicated, attributed, newest-first, and relevance-ranked |
| Republican events | RPT and major-county public calendars | Best-effort because publishers use different calendar systems |
| X posts (command center) | X public syndication profile timelines | No token; merged from a bounded set of legislator handles; rate-limited, cached, with embed/link fallback |
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
- `models.py` — normalized records and source-result contracts
- `data_sources.py` — resilient connectors, parsers, ranking, deduplication, and ICS generation
- `tests/` — parser fixtures, source-contract tests, and UI smoke tests
