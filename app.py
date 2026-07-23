from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from html import escape
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from data_sources import (
    DEFAULT_X_HANDLES,
    LRL_X_LIST,
    PUBLIC_FEED_MAX_ACCOUNTS,
    TEC_FINANCE_HOME,
    dedupe_events,
    dedupe_headlines,
    fetch_events,
    fetch_finance,
    fetch_headlines,
    fetch_hearings,
    fetch_legislative_activity,
    fetch_public_legislator_posts,
    fetch_social_directory,
    fetch_social_posts,
    fetch_x_list_posts,
    make_ics,
)
from models import (
    Headline,
    Hearing,
    LegislatorSocialAccount,
    Milestone,
    PoliticalEvent,
    SourceResult,
    milestone_status,
)


CENTRAL = ZoneInfo("America/Chicago")
TODAY = datetime.now(CENTRAL).date()
SOS_DATES = "https://www.sos.texas.gov/elections/candidates/guide/2026/dates2026.shtml"
TLC_DATES = "https://www.tlc.texas.gov/docs/legref/Dates-of-Interest.pdf"

MILESTONES = (
    Milestone("TEC semiannual report", date(2026, 7, 15), "Campaign finance", TEC_FINANCE_HOME),
    Milestone("Voter registration deadline", date(2026, 10, 5), "Election", SOS_DATES),
    Milestone("30-day campaign report", date(2026, 10, 5), "Campaign finance", TEC_FINANCE_HOME),
    Milestone("Early voting begins", date(2026, 10, 19), "Election", SOS_DATES),
    Milestone("8-day campaign report", date(2026, 10, 26), "Campaign finance", TEC_FINANCE_HOME),
    Milestone("Texas general election", date(2026, 11, 3), "Election", SOS_DATES),
    Milestone("Bill prefiling opens", date(2026, 11, 9), "Legislature", TLC_DATES),
    Milestone("90th Legislature convenes", date(2027, 1, 12), "Legislature", TLC_DATES),
)

st.set_page_config(
    page_title="Lone Star Ledger",
    page_icon="★",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def inject_css() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@400;500;600;700&family=Libre+Caslon+Text:wght@400;700&display=swap');
        :root{--navy:#0a1e36;--blue:#153f68;--red:#b52b35;--ink:#172638;--muted:#647286;
          --paper:#fff;--canvas:#f4f6f8;--line:#dce2e8;--green:#137a52;--amber:#a46305}
        .stApp{background:var(--canvas);color:var(--ink);font-family:'DM Sans',sans-serif}
        .block-container{max-width:1380px;padding:1rem 1.4rem 8rem}
        [data-testid="stHeader"]{background:transparent}[data-testid="stSidebar"]{display:none}
        h1,h2,h3{font-family:'Libre Franklin',sans-serif!important;color:var(--navy);letter-spacing:-.035em}
        .topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:12px}
        .brand{display:flex;align-items:center;gap:11px}.star{width:39px;height:39px;border-radius:11px;
          background:var(--navy);color:#fff;display:grid;place-items:center;font-size:21px}
        .brand-title{font-family:'Libre Franklin';font-weight:800;font-size:19px;color:var(--navy);line-height:1}
        .brand-sub{font-size:10px;color:var(--red);text-transform:uppercase;letter-spacing:.14em;font-weight:800;margin-top:5px}
        .asof{font-size:11px;color:var(--muted);text-align:right}
        .hero{background:linear-gradient(120deg,#091d35,#103a62 72%,#8e2631);border-radius:22px;
          padding:26px 29px;color:#fff;margin:14px 0 16px;position:relative;overflow:hidden}
        .hero:after{content:'★';position:absolute;right:22px;top:-57px;font-size:190px;color:#fff0}
        .hero-kicker{font-size:10px;color:#9dc9ed;text-transform:uppercase;letter-spacing:.14em;font-weight:800}
        .hero h1{color:#fff!important;margin:7px 0 6px;font-size:clamp(25px,3.2vw,40px);max-width:820px}
        .hero p{color:#d7e4ef;font-size:14px;margin:0;max-width:740px}
        .metric{background:#fff;border:1px solid var(--line);border-radius:14px;padding:15px 16px;min-height:102px}
        .metric-label{text-transform:uppercase;letter-spacing:.065em;font-size:10px;color:var(--muted);font-weight:800}
        .metric-value{font-family:'Libre Franklin';font-weight:800;font-size:27px;color:var(--navy);margin:5px 0 2px}
        .metric-note{font-size:11px;color:var(--muted)}
        .section-label{font-family:'Libre Franklin';font-size:19px;font-weight:800;color:var(--navy);margin:25px 0 2px}
        .section-note{font-size:12px;color:var(--muted);margin-bottom:11px}
        .record{background:#fff;border:1px solid var(--line);border-radius:13px;padding:14px 15px;margin-bottom:8px}
        .record:hover{border-color:#aebdcb;box-shadow:0 5px 18px #11283d0c}
        .record-top{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
        .record-title{font-weight:700;font-size:13px;line-height:1.38;color:var(--navy);margin:7px 0 5px}
        .record-title a{color:inherit;text-decoration:none}.record-title a:hover{text-decoration:underline}
        .record-summary{font-size:11.5px;line-height:1.45;color:var(--muted)}
        .tag{font-size:9px;text-transform:uppercase;letter-spacing:.06em;font-weight:800;color:#42546a;
          background:#edf1f5;padding:4px 7px;border-radius:999px}
        .tag-red{background:#fae9eb;color:#9b1f2a}.tag-blue{background:#e8f0f8;color:#164d7d}
        .meta{font-size:10px;color:#758295;margin-top:7px;display:flex;gap:12px;flex-wrap:wrap}
        .status{display:inline-flex;align-items:center;gap:5px;font-size:10px;font-weight:700;color:var(--muted)}
        .status-dot{width:7px;height:7px;border-radius:50%;display:inline-block}
        .live{background:#16805a}.cached{background:#3374aa}.stale{background:#d48712}.unavailable{background:#a3acb7}
        .deadline{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 0;border-bottom:1px solid #edf0f3}
        .deadline:last-child{border:0}.deadline-name{font-size:12px;font-weight:700;color:var(--ink)}
        .deadline-date{font-size:10px;color:var(--muted)}.deadline-days{font-family:'Libre Franklin';font-weight:800;color:var(--navy)}
        .source-card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:13px;margin-bottom:8px}
        .warning-box{background:#fff8e7;border:1px solid #eed698;border-radius:12px;padding:14px;color:#674e12;font-size:12px}
        .empty{background:#fff;border:1px dashed #bfc8d2;border-radius:13px;text-align:center;padding:28px;color:var(--muted);font-size:12px}
        .mobile-only{display:none}
        div[data-testid="stPills"],div[data-testid="stButtonGroup"]{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);
          z-index:1000;width:min(1040px,calc(100vw - 28px));margin:0!important;padding:8px;
          background:rgba(16,28,42,.96);border:1px solid #526171;border-bottom:3px solid #8f2831;
          box-shadow:0 12px 38px rgba(8,20,32,.3);backdrop-filter:blur(12px);overflow-x:auto}
        div[data-testid="stPills"]>div,div[data-testid="stButtonGroup"]>div{min-width:max-content}
        div[data-testid="stPills"] button,div[data-testid="stButtonGroup"] button{font-size:12px;font-weight:700}
        .stButton button,.stDownloadButton button,.stLinkButton a{border-radius:9px;font-weight:700}
        [data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:12px;overflow:hidden}
        footer{display:none}
        /* Editorial intelligence-board treatment */
        .stApp{background:#edf1f4;font-family:'Inter',sans-serif}
        .block-container{max-width:1600px;padding-top:0}
        h1,h2,h3,.section-label,.record-title,.metric-value{font-family:'Libre Caslon Text',Georgia,serif!important;letter-spacing:-.025em}
        .topbar{background:#101c2a;border-bottom:3px solid #8f2831;margin:0 -1.4rem 13px;padding:17px 1.5rem 15px;color:#eef2f5}
        .star{border:1px solid #708094;background:transparent;border-radius:0;font-family:'Libre Caslon Text',serif;font-size:0}
        .star:after{content:'LL';font-size:14px}
        .brand-title{color:#f1f3f4;font-family:'Libre Caslon Text',Georgia,serif;font-size:25px;font-weight:400}
        .brand-sub{color:#aeb9c4;font-family:'IBM Plex Mono',monospace;letter-spacing:.19em}
        .asof{color:#c4ccd4;font-family:'IBM Plex Mono',monospace;text-transform:uppercase}
        div[data-testid="stPills"] button,div[data-testid="stButtonGroup"] button{border-radius:0!important;border-color:#526171!important;
          color:#dce4eb!important;background:#17293b!important;font-family:'IBM Plex Mono',monospace;
          text-transform:uppercase;letter-spacing:.04em;font-size:10px;white-space:nowrap}
        div[data-testid="stPills"] button[aria-pressed="true"],div[data-testid="stButtonGroup"] button[aria-pressed="true"]{background:#f4f6f8!important;
          color:#8f2831!important;border-color:#f4f6f8!important}
        .board-kicker{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.17em;text-transform:uppercase;color:#76242c;font-weight:600}
        .board-title{font-family:'Libre Caslon Text',Georgia,serif;font-size:29px;line-height:1.1;color:#172435;margin:5px 0 8px}
        .board-rule{height:1px;background:#9ca8b3;margin-bottom:11px}
        .countdown-grid{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid #c9d0d7;background:#f8fafb;margin:8px 0 13px}
        .countdown{position:relative;display:grid;grid-template-columns:1fr auto;align-items:center;min-height:84px;padding:11px 18px;border-right:1px solid #d2d8de}
        .countdown:last-child{border-right:0}.countdown-index{position:absolute;left:10px;top:9px;font-family:'IBM Plex Mono';font-size:9px;color:#758190}
        .countdown-date{font-family:'IBM Plex Mono';font-size:9px;letter-spacing:.11em;text-transform:uppercase;color:#76242c;font-weight:600;margin-left:29px}
        .countdown-name{font-family:'Libre Caslon Text',Georgia,serif;font-size:16px;font-weight:700;color:#1d2a3a;margin:4px 0 0 29px}
        .countdown-number{font-family:'Libre Caslon Text',Georgia,serif;font-size:34px;color:#283544;line-height:1;text-align:right}
        .countdown-unit{font-family:'IBM Plex Mono';font-size:8px;letter-spacing:.08em;text-transform:uppercase;margin-left:5px;color:#5d6875}
        .ops-strip{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));background:#16283a;color:#eef3f6;margin:-1px 0 15px;border-bottom:3px solid #8e2932}
        .ops-cell{padding:10px 14px;border-right:1px solid #405063;min-width:0}.ops-cell:last-child{border-right:0}
        .ops-label{font-family:'IBM Plex Mono';font-size:7px;letter-spacing:.13em;text-transform:uppercase;color:#9dacba}
        .ops-value{font-family:'Libre Caslon Text';font-size:16px;font-weight:700;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .ops-note{font-family:'IBM Plex Mono';font-size:7px;color:#aeb9c4;margin-top:3px;text-transform:uppercase}
        .intelligence-grid{display:grid;grid-template-columns:minmax(350px, .9fr) minmax(600px, 1.35fr);gap:17px}
        .hearing-board{background:#f9fafb;border:1px solid #cbd2d9;border-top:3px solid #1c3044;min-height:190px;margin-bottom:8px;display:flex;flex-direction:column}
        .hearing-board.senate{border-top-color:#8e2932}.hearing-head{display:flex;justify-content:space-between;align-items:center;padding:9px 13px;border-bottom:1px solid #d9dee3}
        .chamber-mark{display:flex;gap:10px;align-items:center}.chamber-letter{border:1px solid #7c8997;border-radius:50%;width:34px;height:34px;display:grid;place-items:center;font-family:'Libre Caslon Text';font-size:16px}
        .chamber-state{font-family:'IBM Plex Mono';font-size:8px;letter-spacing:.13em;text-transform:uppercase;color:#788391}
        .chamber-name{font-family:'Libre Caslon Text',Georgia,serif;font-size:20px;font-weight:700;color:#1b2939}
        .meeting-state{font-family:'IBM Plex Mono';font-size:8px;text-transform:uppercase;letter-spacing:.09em;background:#edf0f3;padding:5px 7px;color:#77818c}
        .hearing-body{padding:15px 17px;flex:1}.hearing-empty{display:flex;gap:13px;align-items:flex-start;margin-top:8px}
        .empty-ring{width:30px;height:30px;border:1px solid #b6c0c9;border-radius:50%;position:relative;flex:none}.empty-ring:after{content:'';width:5px;height:5px;border-radius:50%;background:#9aa6b1;position:absolute;left:12px;top:12px}
        .empty-title{font-family:'Libre Caslon Text';font-size:18px;font-weight:700;color:#344252}.empty-copy{font-size:11px;color:#788492;margin-top:5px}
        .hearing-item{padding:8px 0;border-bottom:1px solid #e0e4e8}.hearing-item-title{font-family:'Libre Caslon Text';font-weight:700;font-size:14px;color:#283646}
        .hearing-item-meta{font-family:'IBM Plex Mono';font-size:8px;color:#778492;text-transform:uppercase;margin-top:4px}
        .hearing-foot{border-top:1px solid #d9dee3;padding:9px 14px;font-family:'IBM Plex Mono';font-size:8px;text-transform:uppercase;letter-spacing:.1em;color:#4e5b69}
        .publisher-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
        .publisher-panel{background:#f9fafb;border:1px solid #cbd2d9;border-top:3px solid #273f54;min-height:168px;padding:9px 11px}
        .publisher-panel:nth-child(3n+1){border-top-color:#8e2932}.publisher-head{display:flex;gap:7px;align-items:center;border-bottom:1px solid #d8dde2;padding-bottom:7px;margin-bottom:2px}
        .publisher-monogram{width:25px;height:25px;background:#344b61;color:#fff;display:grid;place-items:center;font-family:'Libre Caslon Text';font-size:10px}
        .publisher-panel:nth-child(3n+1) .publisher-monogram{background:#8e3941}.publisher-name{font-family:'Libre Caslon Text';font-size:13px;font-weight:700;color:#243243;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .headline-row{display:grid;grid-template-columns:18px 1fr auto;gap:7px;padding:6px 0;border-bottom:1px solid #e0e4e8;align-items:start}
        .headline-row:last-child{border-bottom:0}.headline-index{font-family:'IBM Plex Mono';font-size:8px;color:#87919d}
        .headline-copy{font-family:'Libre Caslon Text';font-size:11px;font-weight:700;line-height:1.25;color:#263444;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
        .headline-age{font-family:'IBM Plex Mono';font-size:7px;color:#89939e;white-space:nowrap}.headline-copy a{color:inherit;text-decoration:none}
        .board-footer{display:flex;justify-content:space-between;border-top:1px solid #cbd2d9;margin-top:14px;padding:9px 2px;font-family:'IBM Plex Mono';font-size:8px;letter-spacing:.08em;text-transform:uppercase;color:#6f7b87}
        .connected{color:#167354}.connected:before{content:'';display:inline-block;width:6px;height:6px;background:#1d9168;border-radius:50%;margin-right:6px}
        .lower-board{background:#f9fafb;border:1px solid #cbd2d9;border-top:3px solid #8e2932;padding:10px 13px;height:286px;overflow:auto}
        .field-row{display:grid;grid-template-columns:58px 1fr auto;gap:11px;align-items:start;padding:8px 0;border-bottom:1px solid #dde2e7}
        .field-calendar-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:22px}
        .field-row:last-child{border-bottom:0}.field-date{font-family:'IBM Plex Mono';font-size:8px;color:#7c2730;text-transform:uppercase;line-height:1.4;background:#f0e7e8;border-left:2px solid #8e2932;padding:5px 6px}
        .field-title{font-family:'Libre Caslon Text';font-weight:700;font-size:12px;color:#283646;line-height:1.3}
        .field-meta{font-size:9px;color:#798592;margin-top:4px}.field-region{font-family:'IBM Plex Mono';font-size:8px;color:#6d7884;text-transform:uppercase;white-space:nowrap}
        .event-kind{display:inline-block;margin-top:5px;padding:2px 5px;background:#e8edf1;color:#4c5a68;font-family:'IBM Plex Mono';font-size:7px;text-transform:uppercase;letter-spacing:.05em}
        .x-connect{font-family:'IBM Plex Mono';font-size:8px;text-transform:uppercase;letter-spacing:.08em;color:#157253;margin-bottom:8px}
        .x-roster-note{font-size:10px;line-height:1.45;color:#667483;padding:3px 0 7px}
        @media(max-width:700px){
          .block-container{padding:.7rem .75rem 8rem}.topbar{align-items:flex-start}.asof{display:none}
          .star{width:35px;height:35px}.brand-title{font-size:16px}.hero{padding:21px 18px;border-radius:17px}
          .hero h1{font-size:26px}.metric{min-height:88px;padding:12px}.metric-value{font-size:23px}
          div[data-testid="stHorizontalBlock"]{gap:.55rem;flex-wrap:wrap}
          div[data-testid="stHorizontalBlock"]>div{min-width:145px;flex:1 1 45%}
          .record{padding:13px}.record-summary{font-size:12px}
          [data-testid="stDataFrame"]{display:none}
          .mobile-only{display:block}
          .topbar{margin:0 -.75rem 10px;padding:14px .85rem}.brand-title{font-size:20px}
          .countdown-grid{grid-template-columns:1fr}.countdown{min-height:82px;border-right:0;border-bottom:1px solid #d2d8de}
          .countdown:last-child{border-bottom:0}.countdown-number{font-size:31px}.countdown-name{font-size:16px}
          .ops-strip{grid-template-columns:repeat(2,1fr)}.ops-cell:nth-child(2){border-right:0}.ops-cell{border-bottom:1px solid #405063}
          .intelligence-grid{grid-template-columns:1fr}.publisher-grid{grid-template-columns:1fr}
          .hearing-board{min-height:210px}.publisher-panel{min-height:0}.board-title{font-size:25px}
          .lower-board{height:auto;max-height:410px}.field-row{grid-template-columns:52px 1fr}.field-region{display:none}
          .field-calendar-grid{grid-template-columns:1fr}
          div[data-testid="stPills"],div[data-testid="stButtonGroup"]{left:8px;right:8px;bottom:8px;transform:none;width:auto;padding:6px}
          div[data-testid="stPills"] button,div[data-testid="stButtonGroup"] button{font-size:9px;min-height:38px}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def get_secret(name: str, default: Any = None) -> Any:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def configured_x_token() -> str:
    runtime = st.session_state.get("runtime_x_bearer_token", "")
    return str(runtime or get_secret("X_BEARER_TOKEN", "") or "")


@st.cache_data(ttl=900, show_spinner=False)
def live_activity() -> list[SourceResult]:
    return fetch_legislative_activity()


@st.cache_data(ttl=900, show_spinner=False)
def live_hearings() -> list[SourceResult]:
    return fetch_hearings()


@st.cache_data(ttl=900, show_spinner=False)
def live_headlines() -> list[SourceResult]:
    return fetch_headlines()


@st.cache_data(ttl=1800, show_spinner=False)
def live_events() -> list[SourceResult]:
    return fetch_events()


@st.cache_data(ttl=21600, show_spinner=False)
def live_finance() -> SourceResult:
    return fetch_finance()


@st.cache_data(ttl=86400, show_spinner=False)
def live_directory() -> SourceResult:
    return fetch_social_directory()


@st.cache_data(ttl=300, show_spinner=False)
def live_posts(
    token: str, base_url: str, accounts: tuple[LegislatorSocialAccount, ...]
) -> SourceResult:
    return fetch_social_posts(token, base_url, list(accounts))


@st.cache_data(ttl=300, show_spinner=False)
def live_x_list_posts(token: str, base_url: str) -> SourceResult:
    return fetch_x_list_posts(token, base_url)


@st.cache_data(ttl=300, show_spinner=False)
def live_public_posts(accounts: tuple[LegislatorSocialAccount, ...]) -> SourceResult:
    return fetch_public_legislator_posts(list(accounts))


def public_feed_accounts(
    directory_items: list[LegislatorSocialAccount],
) -> list[LegislatorSocialAccount]:
    """Ordered legislator accounts for the no-token public feed.

    Prefers configured default handles, then fills from the live LRL directory. Falls
    back to a small built-in handle set only if the directory is unavailable.
    """
    defaults = get_secret("SOCIAL_DEFAULT_HANDLES", get_secret("TXLEGE_X_HANDLES", []))
    default_handles = [str(x).lstrip("@") for x in defaults] if isinstance(defaults, list) else []
    if directory_items:
        by_handle = {a.handle.lower(): a for a in directory_items}
        ordered = [by_handle[h.lower()] for h in default_handles if h.lower() in by_handle]
        seen = {a.handle.lower() for a in ordered}
        for account in directory_items:
            if account.handle.lower() not in seen:
                ordered.append(account)
                seen.add(account.handle.lower())
    else:
        handles = default_handles or list(DEFAULT_X_HANDLES)
        ordered = [
            LegislatorSocialAccount(name=h, chamber="", handle=h, profile_url=f"https://x.com/{h}")
            for h in handles
        ]
    return ordered[:PUBLIC_FEED_MAX_ACCOUNTS]


def remember(results: SourceResult | Iterable[SourceResult]) -> None:
    values = [results] if isinstance(results, SourceResult) else list(results)
    health = st.session_state.setdefault("source_health", {})
    for result in values:
        health[result.source_name] = result


def flatten(results: Iterable[SourceResult]) -> list[Any]:
    return [item for result in results for item in result.items]


def fmt_time(value: datetime | None, include_date: bool = True) -> str:
    if not value:
        return "Time not supplied"
    value = value.astimezone(CENTRAL)
    pattern = "%b %d · %I:%M %p" if include_date else "%I:%M %p"
    return value.strftime(pattern).replace(" 0", " ")


def safe_url(value: str) -> str:
    return escape(value if value.startswith("https://") else "#", quote=True)


def status_line(result: SourceResult) -> None:
    checked = fmt_time(result.fetched_at) if result.fetched_at else "Not checked"
    st.markdown(
        f'<span class="status"><span class="status-dot {result.freshness}"></span>'
        f'{escape(result.freshness.title())} · {len(result.items)} records · {checked}</span>',
        unsafe_allow_html=True,
    )


def empty_state(message: str, result: SourceResult | None = None) -> None:
    detail = f" {result.error}" if result and result.error else ""
    st.markdown(f'<div class="empty">{escape(message + detail)}</div>', unsafe_allow_html=True)


def metric(label: str, value: str, note: str) -> None:
    st.markdown(
        f'<div class="metric"><div class="metric-label">{escape(label)}</div>'
        f'<div class="metric-value">{escape(value)}</div><div class="metric-note">{escape(note)}</div></div>',
        unsafe_allow_html=True,
    )


def section(title: str, note: str) -> None:
    st.markdown(
        f'<div class="section-label">{escape(title)}</div><div class="section-note">{escape(note)}</div>',
        unsafe_allow_html=True,
    )


def headline_card(item: Headline) -> None:
    st.markdown(
        f"""<div class="record"><div class="record-top">
        <span class="tag tag-red">{escape(item.publisher)}</span>
        <span class="tag">priority {item.relevance}</span></div>
        <div class="record-title"><a href="{safe_url(item.url)}" target="_blank">{escape(item.title)} ↗</a></div>
        <div class="meta"><span>{escape(fmt_time(item.published_at))}</span></div></div>""",
        unsafe_allow_html=True,
    )


def hearing_card(item: Hearing) -> None:
    st.markdown(
        f"""<div class="record"><div class="record-top"><span class="tag tag-blue">{escape(item.chamber)}</span>
        <span class="tag">{escape(item.committee or 'Committee')}</span></div>
        <div class="record-title"><a href="{safe_url(item.url)}" target="_blank">{escape(item.title)} ↗</a></div>
        <div class="record-summary">{escape(item.summary[:260])}</div>
        <div class="meta"><span>{escape(fmt_time(item.starts_at))}</span><span>{escape(item.location)}</span></div></div>""",
        unsafe_allow_html=True,
    )


def event_card(item: PoliticalEvent) -> None:
    st.markdown(
        f"""<div class="record"><div class="record-top"><span class="tag tag-red">{escape(item.region)}</span>
        <span class="tag">{escape(item.event_type)}</span></div>
        <div class="record-title"><a href="{safe_url(item.url)}" target="_blank">{escape(item.title)} ↗</a></div>
        <div class="meta"><span>{escape(fmt_time(item.starts_at))}</span><span>{escape(item.organizer)}</span>
        <span>{escape(' · '.join(x for x in [item.venue, item.address] if x))}</span></div></div>""",
        unsafe_allow_html=True,
    )


def milestone_row(item: Milestone) -> None:
    days, _ = milestone_status(item.occurs_on, TODAY)
    st.markdown(
        f"""<div class="deadline"><div><div class="deadline-name">{escape(item.name)}</div>
        <div class="deadline-date">{item.occurs_on.strftime('%b %d, %Y')} · {escape(item.category)}</div></div>
        <div class="deadline-days">{escape(days)}</div></div>""",
        unsafe_allow_html=True,
    )


def compact_age(value: datetime | None) -> str:
    if not value:
        return "recent"
    seconds = max(0, int((datetime.now(CENTRAL) - value.astimezone(CENTRAL)).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def countdown_strip() -> None:
    targets = [
        ("General Election", date(2026, 11, 3), "November 3, 2026"),
        ("First Day of Bill Filing", date(2026, 11, 9), "November 9, 2026"),
        ("90th Legislative Session", date(2027, 1, 12), "January 12, 2027"),
    ]
    cards = []
    now = datetime.now(CENTRAL)
    for index, (name, target, date_label) in enumerate(targets, 1):
        target_time = datetime.combine(target, datetime.min.time(), tzinfo=CENTRAL)
        if name == "90th Legislative Session":
            target_time = target_time.replace(hour=12)
        remaining = max(0, int((target_time - now).total_seconds()))
        days, hours = remaining // 86400, (remaining % 86400) // 3600
        state = "Complete" if target < TODAY else f"{days}"
        unit = "complete" if target < TODAY else f"days<br>{hours} hrs"
        cards.append(
            f"""<div class="countdown"><span class="countdown-index">0{index}</span>
            <div><div class="countdown-date">{escape(date_label)}</div>
            <div class="countdown-name">{escape(name)}</div></div>
            <div><span class="countdown-number">{escape(str(state))}</span>
            <span class="countdown-unit">{unit}</span></div></div>"""
        )
    st.markdown(f'<div class="countdown-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def operations_strip(
    hearings: list[Hearing], headlines: list[Headline], events: list[PoliticalEvent]
) -> None:
    now = datetime.now(CENTRAL)
    week_end = TODAY + timedelta(days=6)
    week_hearings = sum(
        1
        for item in hearings
        if item.starts_at
        and TODAY <= item.starts_at.astimezone(CENTRAL).date() <= week_end
    )
    fresh_headlines = sum(
        1
        for item in headlines
        if item.published_at and item.published_at >= now - timedelta(hours=24)
    )
    next_event = next((item for item in events if item.starts_at and item.starts_at >= now), None)
    next_event_value = next_event.title if next_event else "No dated event"
    next_event_note = fmt_time(next_event.starts_at) if next_event else "Check source calendars"
    organizations = len({item.organizer for item in events if item.organizer})
    st.markdown(
        f"""<div class="ops-strip">
        <div class="ops-cell"><div class="ops-label">Hearings this week</div>
        <div class="ops-value">{week_hearings}</div><div class="ops-note">Next seven days · House + Senate</div></div>
        <div class="ops-cell"><div class="ops-label">New reporting</div>
        <div class="ops-value">{fresh_headlines}</div><div class="ops-note">Published in 24 hours</div></div>
        <div class="ops-cell"><div class="ops-label">Next field event</div>
        <div class="ops-value">{escape(next_event_value)}</div><div class="ops-note">{escape(next_event_note)}</div></div>
        <div class="ops-cell"><div class="ops-label">GOP network</div>
        <div class="ops-value">{organizations} organizations</div><div class="ops-note">{len(events)} dated events</div></div>
        </div>""",
        unsafe_allow_html=True,
    )


def hearing_board(chamber: str, hearings: list[Hearing]) -> str:
    letter = chamber[0]
    week_end = TODAY + timedelta(days=6)
    week_items = [
        item for item in hearings
        if item.chamber == chamber
        and item.starts_at
        and TODAY <= item.starts_at.astimezone(CENTRAL).date() <= week_end
    ]
    if week_items:
        body = "".join(
            f"""<div class="hearing-item"><div class="hearing-item-title">
            <a href="{safe_url(item.url)}" target="_blank">{escape(item.committee)}</a></div>
            <div class="hearing-item-meta">{escape(fmt_time(item.starts_at))} · {escape(item.location)}</div></div>"""
            for item in week_items[:6]
        )
        meeting_state = f"{len(week_items)} this week"
    else:
        body = f"""<div class="hearing-empty"><span class="empty-ring"></span><div>
        <div class="empty-title">No hearings this week</div>
        <div class="empty-copy">No {escape(chamber)} committee meetings are posted for the next seven days.</div></div></div>"""
        meeting_state = "None this week"
    calendar_url = (
        "https://capitol.texas.gov/Committees/MeetingsUpcoming.aspx?Chamber=H"
        if chamber == "House"
        else "https://capitol.texas.gov/Committees/MeetingsUpcoming.aspx?Chamber=S"
    )
    return f"""<div class="hearing-board {'senate' if chamber == 'Senate' else ''}">
    <div class="hearing-head"><div class="chamber-mark"><span class="chamber-letter">{letter}</span><div>
    <div class="chamber-state">Texas</div><div class="chamber-name">{chamber}</div></div></div>
    <span class="meeting-state">{escape(meeting_state)}</span></div>
    <div class="hearing-body">{body}</div>
    <div class="hearing-foot"><a href="{calendar_url}" target="_blank">Official {chamber} calendar →</a></div></div>"""


def publisher_panels(headlines: list[Headline]) -> str:
    by_publisher: dict[str, list[Headline]] = {}
    for item in headlines:
        if not item.publisher or item.publisher == "Unknown publisher":
            continue
        by_publisher.setdefault(item.publisher, []).append(item)
    ranked_by_recency = sorted(
        by_publisher,
        key=lambda name: (
            max(
                (item.published_at.timestamp() for item in by_publisher[name] if item.published_at),
                default=0,
            ),
            max((item.relevance for item in by_publisher[name]), default=0),
        ),
        reverse=True,
    )
    priority_publishers = [
        "The Texas Tribune",
        "The Texan",
        "Texas Bullpen",
        "Texas Scorecard",
        "Current Revolt",
    ]
    ranked_publishers = [
        *[name for name in priority_publishers if name in by_publisher],
        *[name for name in ranked_by_recency if name not in priority_publishers],
    ][:6]
    panels = []
    for publisher in ranked_publishers:
        monogram = "".join(word[0] for word in publisher.replace("The ", "").split()[:3]).upper()
        rows = []
        for index, item in enumerate(by_publisher[publisher][:3], 1):
            rows.append(
                f"""<div class="headline-row"><span class="headline-index">0{index}</span>
                <div class="headline-copy"><a href="{safe_url(item.url)}" target="_blank">{escape(item.title)}</a></div>
                <span class="headline-age">{escape(compact_age(item.published_at))}</span></div>"""
            )
        panels.append(
            f"""<div class="publisher-panel"><div class="publisher-head">
            <span class="publisher-monogram">{escape(monogram)}</span>
            <span class="publisher-name">{escape(publisher)}</span></div>{"".join(rows)}</div>"""
        )
    if not panels:
        return '<div class="empty">Headline sources are temporarily unavailable.</div>'
    return f'<div class="publisher-grid">{"".join(panels)}</div>'


def field_calendar_panel(events: list[PoliticalEvent]) -> str:
    if not events:
        return '<div class="empty">No upcoming Republican events were returned.</div>'
    rows = []
    for item in events[:8]:
        date_label = (
            item.starts_at.astimezone(CENTRAL).strftime("%b %d<br>%I:%M %p").replace(" 0", " ")
            if item.starts_at
            else "Date<br>pending"
        )
        rows.append(
            f"""<div class="field-row"><div class="field-date">{date_label}</div><div>
            <div class="field-title"><a href="{safe_url(item.url)}" target="_blank">{escape(item.title)} ↗</a></div>
            <div class="field-meta">{escape(item.organizer)}{(' · ' + escape(item.venue)) if item.venue else ''}</div>
            <span class="event-kind">{escape(item.event_type)}</span></div>
            <div class="field-region">{escape(item.region)}</div></div>"""
        )
    return f'<div class="field-calendar-grid">{"".join(rows)}</div>'


def social_posts_panel(posts: list[Any]) -> str:
    if not posts:
        return '<div class="empty">Connect X API read access to load the live legislator list.</div>'
    rows = []
    for post in posts[:8]:
        rows.append(
            f"""<div class="field-row"><div class="field-date">{escape(compact_age(post.created_at))}<br>ago</div><div>
            <div class="field-title"><a href="{safe_url(post.url)}" target="_blank">
            {escape(post.legislator_name)} · @{escape(post.handle)} ↗</a></div>
            <div class="field-meta">{escape(post.text[:180])}</div></div>
            <div class="field-region">{post.likes} ♥ · {post.reposts} RP</div></div>"""
        )
    return "".join(rows)


def social_directory_panel(accounts: list[LegislatorSocialAccount]) -> str:
    if not accounts:
        return '<div class="empty">The official LRL account directory is temporarily unavailable.</div>'
    rows = []
    for account in accounts[:12]:
        rows.append(
            f"""<div class="field-row"><div class="field-date">{escape(account.chamber)}</div><div>
            <div class="field-title"><a href="{safe_url(account.profile_url)}" target="_blank">
            {escape(account.name)} ↗</a></div>
            <div class="field-meta">@{escape(account.handle)}</div></div>
            <div class="field-region">X profile</div></div>"""
        )
    return "".join(rows)


def load_command_data() -> tuple[list[SourceResult], list[SourceResult], list[SourceResult], list[SourceResult], SourceResult]:
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_activity = executor.submit(live_activity)
        future_hearings = executor.submit(live_hearings)
        future_headlines = executor.submit(live_headlines)
        future_events = executor.submit(live_events)
        future_finance = executor.submit(live_finance)
        return (
            future_activity.result(),
            future_hearings.result(),
            future_headlines.result(),
            future_events.result(),
            future_finance.result(),
        )


def command_center() -> None:
    with ThreadPoolExecutor(max_workers=3) as executor:
        hearing_future = executor.submit(live_hearings)
        headline_future = executor.submit(live_headlines)
        event_future = executor.submit(live_events)
        hearing_results = hearing_future.result()
        headline_results = headline_future.result()
        event_results = event_future.result()
    remember(hearing_results)
    remember(headline_results)
    remember(event_results)
    hearings = flatten(hearing_results)
    headlines = dedupe_headlines(headline_results)
    events = dedupe_events(event_results)
    countdown_strip()
    operations_strip(hearings, headlines, events)
    st.markdown(
        f"""<div class="intelligence-grid"><section><div class="board-kicker">Texas Legislature</div>
        <div class="board-title">This Week’s Hearings</div><div class="board-rule"></div>
        {hearing_board("House", hearings)}{hearing_board("Senate", hearings)}</section>
        <section><div class="board-kicker">Across Texas</div>
        <div class="board-title">Top Headlines</div><div class="board-rule"></div>
        {publisher_panels(headlines)}</section></div>
        <div class="board-footer"><span class="connected">Live data connected</span>
        <span>Hearing data: Texas Legislature Online · News updates every 15 minutes</span>
        <span>Updated {datetime.now(CENTRAL).strftime('%I:%M %p').lstrip('0')} CT</span></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class="board-kicker">Republican Field Network</div>
        <div class="board-title">Upcoming GOP Events</div><div class="board-rule"></div>
        <div class="lower-board">{field_calendar_panel(events)}</div>""",
        unsafe_allow_html=True,
    )
    st.download_button(
        "Download GOP calendar (.ics)",
        make_ics(events, "Texas Republican field calendar"),
        "texas-gop-calendar.ics",
        "text/calendar",
        width="stretch",
    )


def legislature_page() -> None:
    st.markdown("## Legislative monitor")
    st.caption("Official Texas Legislature Online activity and committee notices.")
    activity_results, hearing_results = live_activity(), live_hearings()
    remember(activity_results)
    remember(hearing_results)
    activity, hearings = flatten(activity_results), flatten(hearing_results)
    tab_activity, tab_hearings, tab_dates = st.tabs(["Activity", "Hearings", "Deadlines"])

    with tab_activity:
        c1, c2, c3 = st.columns([1.1, 1, 2])
        with c1:
            chamber = st.selectbox("Chamber", ["All", "House", "Senate", "Both"])
        with c2:
            activity_type = st.selectbox(
                "Activity", ["All"] + sorted({x.activity_type for x in activity})
            )
        with c3:
            query = st.text_input("Search activity", placeholder="Bill number, caption, or keyword")
        shown = [
            x for x in activity
            if (chamber == "All" or x.chamber == chamber)
            and (activity_type == "All" or x.activity_type == activity_type)
            and (not query or query.lower() in f"{x.title} {x.summary}".lower())
        ]
        if shown:
            for item in shown[:50]:
                st.markdown(
                    f"""<div class="record"><div class="record-top"><span class="tag tag-blue">{escape(item.chamber)}</span>
                    <span class="tag">{escape(item.activity_type)}</span></div>
                    <div class="record-title"><a href="{safe_url(item.url)}" target="_blank">{escape(item.title)} ↗</a></div>
                    <div class="record-summary">{escape(item.summary[:320])}</div>
                    <div class="meta">{escape(fmt_time(item.published_at))}</div></div>""",
                    unsafe_allow_html=True,
                )
        else:
            empty_state("No legislative activity matches these filters.")

    with tab_hearings:
        c1, c2, c3 = st.columns([1, 1.4, 2])
        with c1:
            h_chamber = st.selectbox("Hearing chamber", ["All", "House", "Senate"])
        with c2:
            committees = ["All"] + sorted({x.committee for x in hearings if x.committee})
            committee = st.selectbox("Committee", committees)
        with c3:
            h_query = st.text_input("Search notices", placeholder="Committee, location, or subject")
        shown_hearings = [
            x for x in hearings
            if (h_chamber == "All" or x.chamber == h_chamber)
            and (committee == "All" or x.committee == committee)
            and (not h_query or h_query.lower() in f"{x.title} {x.summary} {x.location}".lower())
        ]
        if shown_hearings:
            st.download_button(
                "Download filtered hearings (.ics)",
                make_ics(shown_hearings, "Texas legislative hearings"),
                "texas-legislative-hearings.ics",
                "text/calendar",
            )
            for item in shown_hearings:
                hearing_card(item)
        else:
            empty_state("No hearing notices match these filters.")

    with tab_dates:
        for milestone in MILESTONES:
            milestone_row(milestone)
        st.link_button("Texas Legislative Council dates ↗", TLC_DATES)


def finance_page() -> None:
    st.markdown("## Campaign finance")
    st.caption("Current statewide PAC contribution and expenditure totals published by the Texas Ethics Commission.")
    result = live_finance()
    remember(result)
    status_line(result)
    if not result.items:
        empty_state("The TEC finance workbook is unavailable.", result)
        st.link_button("Search official campaign-finance records ↗", TEC_FINANCE_HOME)
        return
    st.caption(result.items[0].reporting_period)
    query_col, type_col, state_col = st.columns([2, 1, 1])
    with query_col:
        query = st.text_input("Search filer", placeholder="PAC or filer name")
    with type_col:
        filer_type = st.selectbox("Filer type", ["All"] + sorted({x.filer_type for x in result.items}))
    with state_col:
        state_filter = st.selectbox("State", ["All", "TX", "Outside Texas"])
    shown = [
        x for x in result.items
        if (not query or query.lower() in x.filer_name.lower())
        and (filer_type == "All" or x.filer_type == filer_type)
        and (
            state_filter == "All"
            or (state_filter == "TX" and x.state == "TX")
            or (state_filter == "Outside Texas" and x.state != "TX")
        )
    ]
    total_in = sum(x.contributions for x in shown)
    total_out = sum(x.expenditures for x in shown)
    cols = st.columns(3)
    with cols[0]:
        metric("Contributions", f"${total_in / 1_000_000:,.1f}M", f"{len(shown):,} matching filers")
    with cols[1]:
        metric("Expenditures", f"${total_out / 1_000_000:,.1f}M", "reported political spending")
    with cols[2]:
        metric("Net activity", f"${(total_in-total_out) / 1_000_000:,.1f}M", "receipts minus expenditures")
    frame = pd.DataFrame(
        [
            {
                "Rank": x.rank,
                "Filer": x.filer_name,
                "Type": x.filer_type,
                "City": x.city,
                "State": x.state,
                "Contributions": x.contributions,
                "Expenditures": x.expenditures,
                "Net activity": x.net_activity,
            }
            for x in shown
        ]
    )
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=500,
        column_config={
            "Contributions": st.column_config.NumberColumn(format="$%.0f"),
            "Expenditures": st.column_config.NumberColumn(format="$%.0f"),
            "Net activity": st.column_config.NumberColumn(format="$%.0f"),
        },
    )
    mobile_cards = "".join(
        f"""<div class="record"><div class="record-top"><span class="tag tag-red">#{item.rank}</span>
            <span class="tag">{escape(item.filer_type)}</span></div><div class="record-title">{escape(item.filer_name)}</div>
            <div class="meta"><span>Received ${item.contributions:,.0f}</span>
            <span>Spent ${item.expenditures:,.0f}</span><span>{escape(item.city)}, {escape(item.state)}</span></div></div>"""
        for item in shown[:25]
    )
    st.markdown(
        f'<div class="mobile-only"><div class="section-label">Results</div>{mobile_cards}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="warning-box"><b>Interpretation note:</b> These are reported PAC totals, not candidate race totals. '
        "Cash on hand, amendments, paper filings, and reporting periods require review in the signed official record.</div>",
        unsafe_allow_html=True,
    )
    st.link_button("Search filings and cash on hand at TEC ↗", TEC_FINANCE_HOME)


def headlines_page() -> None:
    st.markdown("## Political media monitor")
    st.caption("Attributed Texas political coverage, deduplicated and ranked for Republican operational relevance.")
    results = live_headlines()
    remember(results)
    items = dedupe_headlines(results)
    publishers = ["All"] + sorted({x.publisher for x in items})
    c1, c2 = st.columns([1, 2])
    with c1:
        publisher = st.selectbox("Publisher", publishers)
    with c2:
        query = st.text_input("Search headlines", placeholder="Issue, official, county, or race")
    shown = [
        x for x in items
        if (publisher == "All" or x.publisher == publisher)
        and (not query or query.lower() in f"{x.title} {x.summary}".lower())
    ]
    if shown:
        for item in shown[:50]:
            headline_card(item)
    else:
        empty_state("No headlines match these filters.")


def social_page() -> None:
    st.markdown("## Texas legislators on X")
    st.caption("Account identities come from the Legislative Reference Library; post content belongs to each account.")
    with st.expander("Connect or update X API access"):
        entered_token = st.text_input(
            "X API bearer token",
            type="password",
            placeholder="Paste a bearer token with post-read access",
            help="Stored only in this browser session. For deployment, use Streamlit secrets.",
        )
        connect_col, clear_col = st.columns(2)
        with connect_col:
            if st.button("Connect X API", width="stretch"):
                if entered_token.strip():
                    st.session_state["runtime_x_bearer_token"] = entered_token.strip()
                    st.cache_data.clear()
                    st.rerun()
        with clear_col:
            if st.button("Use embedded public feed", width="stretch"):
                st.session_state.pop("runtime_x_bearer_token", None)
                st.cache_data.clear()
                st.rerun()
    directory = live_directory()
    remember(directory)
    status_line(directory)
    if not directory.items:
        empty_state("The LRL legislator directory is unavailable.", directory)
        return
    c1, c2 = st.columns([1, 2])
    with c1:
        chamber = st.selectbox("Social chamber", ["All", "House", "Senate"])
    with c2:
        query = st.text_input("Find a legislator", placeholder="Name or X handle")
    filtered = [
        x for x in directory.items
        if (chamber == "All" or x.chamber == chamber)
        and (not query or query.lower() in f"{x.name} {x.handle}".lower())
    ]
    defaults = get_secret(
        "SOCIAL_DEFAULT_HANDLES",
        get_secret("TXLEGE_X_HANDLES", []),
    )
    default_handles = {str(x).lstrip("@").lower() for x in defaults} if isinstance(defaults, list) else set()
    default_accounts = [x for x in directory.items if x.handle.lower() in default_handles][:10]
    options = {f"{x.name} · @{x.handle}": x for x in filtered}
    default_labels = [
        label for label, account in options.items() if account.handle.lower() in default_handles
    ]
    selected_labels = st.multiselect(
        "Live-feed accounts (maximum 10)",
        list(options),
        default=default_labels,
        max_selections=10,
    )
    selected = [options[label] for label in selected_labels] or default_accounts
    token = configured_x_token()
    base_url = str(get_secret("X_API_BASE_URL", "https://api.x.com") or "https://api.x.com")
    tab_feed, tab_directory = st.tabs(["Recent posts", f"Directory ({len(filtered)})"])
    with tab_feed:
        posts = live_posts(token, base_url, tuple(selected))
        remember(posts)
        if posts.items:
            status_line(posts)
            for post in posts.items:
                st.markdown(
                    f"""<div class="record"><div class="record-top"><span class="tag tag-blue">
                    {escape(post.legislator_name)}</span><span class="tag">@{escape(post.handle)}</span></div>
                    <div class="record-summary" style="margin-top:8px">{escape(post.text)}</div>
                    <div class="meta"><span>{escape(fmt_time(post.created_at))}</span>
                    <span>{post.likes} likes</span><span>{post.reposts} reposts</span>
                    <a href="{safe_url(post.url)}" target="_blank">Open post ↗</a></div></div>""",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                '<div class="warning-box"><b>Live X API access is not connected.</b> Add a bearer token with '
                "post-read access above. X’s public embedded list is rate-limited and is not used as a false live fallback.</div>",
                unsafe_allow_html=True,
            )
            st.link_button("Open the LRL legislator list on X ↗", LRL_X_LIST)
    with tab_directory:
        for account in filtered:
            st.markdown(
                f"""<div class="record"><div class="record-top"><span class="tag tag-blue">{escape(account.chamber)}</span></div>
                <div class="record-title"><a href="{safe_url(account.profile_url)}" target="_blank">
                {escape(account.name)} · @{escape(account.handle)} ↗</a></div></div>""",
                unsafe_allow_html=True,
            )


def events_page() -> None:
    st.markdown("## Republican field calendar")
    st.caption(
        "Dated events from official state, county, federated Republican women, club, and committee calendars."
    )
    results = live_events()
    remember(results)
    events = dedupe_events(results)
    organizations = sorted({x.organizer for x in events if x.organizer})
    upcoming_30 = sum(
        1
        for x in events
        if x.starts_at and TODAY <= x.starts_at.astimezone(CENTRAL).date() <= TODAY + timedelta(days=30)
    )
    summary_cols = st.columns(4)
    with summary_cols[0]:
        metric("Dated events", str(len(events)), "deduplicated")
    with summary_cols[1]:
        metric("Organizations", str(len(organizations)), "named hosts")
    with summary_cols[2]:
        metric("Next 30 days", str(upcoming_30), "field activity")
    with summary_cols[3]:
        metric(
            "Sources online",
            f"{sum(1 for x in results if x.ok)}/{len(results)}",
            "live or cached",
        )
    c1, c2, c3, c4 = st.columns([1, 1, 1.35, 1.65])
    with c1:
        region = st.selectbox("Region", ["All"] + sorted({x.region for x in events}))
    with c2:
        event_type = st.selectbox("Event type", ["All"] + sorted({x.event_type for x in events}))
    with c3:
        organizer = st.selectbox("Organization", ["All"] + organizations)
    with c4:
        query = st.text_input("Search events", placeholder="Club, organizer, venue, or keyword")
    shown = [
        x for x in events
        if (region == "All" or x.region == region)
        and (event_type == "All" or x.event_type == event_type)
        and (organizer == "All" or x.organizer == organizer)
        and (not query or query.lower() in f"{x.title} {x.organizer} {x.venue}".lower())
    ]
    if shown:
        st.download_button(
            "Download filtered calendar (.ics)",
            make_ics(shown, "Texas Republican field calendar"),
            "texas-republican-field-calendar.ics",
            "text/calendar",
        )
        event_columns = st.columns(2)
        for index, item in enumerate(shown):
            with event_columns[index % 2]:
                event_card(item)
    else:
        empty_state("No upcoming events match these filters.")
    with st.expander("Connected sources and status"):
        for result in sorted(results, key=lambda x: x.source_name):
            status_line(result)
            st.link_button(f"Open {result.source_name} ↗", result.source_url)


def source_health_page() -> None:
    st.markdown("## Source health")
    st.caption("Runtime status for authoritative and intelligence feeds checked during this session.")
    if st.button("Check every source now", type="primary"):
        with st.spinner("Checking sources…"):
            activity, hearings, headlines, events, finance = load_command_data()
            directory = live_directory()
            for group in (activity, hearings, headlines, events):
                remember(group)
            remember(finance)
            remember(directory)
    health = st.session_state.get("source_health", {})
    if not health:
        empty_state("No sources have been checked in this session.")
        return
    for result in sorted(health.values(), key=lambda x: x.source_name):
        checked = fmt_time(result.fetched_at) if result.fetched_at else "Not checked"
        st.markdown(
            f"""<div class="source-card"><div class="record-top">
            <span class="status"><span class="status-dot {result.freshness}"></span>{escape(result.freshness.title())}</span>
            <span class="tag">{len(result.items)} records</span><span class="tag">{result.latency_ms} ms</span></div>
            <div class="record-title"><a href="{safe_url(result.source_url)}" target="_blank">{escape(result.source_name)} ↗</a></div>
            <div class="record-summary">{escape(result.error or 'Source responded normally.')}</div>
            <div class="meta">{escape(checked)}</div></div>""",
            unsafe_allow_html=True,
        )


inject_css()
st.markdown(
    f"""<div class="topbar"><div class="brand"><div class="star">★</div><div>
    <div class="brand-title">Lone Star Ledger</div>
    <div class="brand-sub">Texas legislative intelligence</div></div></div>
    <div class="asof">America/Chicago<br>{datetime.now(CENTRAL).strftime('%B %d, %Y · %I:%M %p').replace(' 0', ' ')}</div></div>""",
    unsafe_allow_html=True,
)

pages = ["Command center", "Legislature", "Campaign finance", "Media", "GOP calendar", "Source health"]
if st.session_state.get("bottom_navigation") not in pages:
    st.session_state["bottom_navigation"] = "Command center"
page = st.session_state["bottom_navigation"]

if page == "Command center":
    command_center()
elif page == "Legislature":
    legislature_page()
elif page == "Campaign finance":
    finance_page()
elif page == "Media":
    headlines_page()
elif page == "GOP calendar":
    events_page()
else:
    source_health_page()

st.pills(
    "Navigation",
    pages,
    key="bottom_navigation",
    label_visibility="collapsed",
)
