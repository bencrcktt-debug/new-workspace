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
    PUBLIC_INDEX_MAX_ACCOUNTS,
    TEC_FINANCE_HOME,
    dedupe_events,
    dedupe_headlines,
    extract_topics,
    fetch_events,
    fetch_finance,
    fetch_government_intelligence,
    fetch_headlines,
    fetch_hearings,
    fetch_influence_intelligence,
    fetch_legislative_activity,
    fetch_public_legislator_posts,
    fetch_social_directory,
    fetch_social_posts,
    fetch_x_list_posts,
    headline_priority,
    make_briefing,
    make_ics,
    select_action_records,
)
from models import (
    FinanceSummary,
    GovernmentRecord,
    Headline,
    Hearing,
    LegislatorSocialAccount,
    Milestone,
    PoliticalEvent,
    SourceResult,
    milestone_status,
    next_milestones,
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
    Milestone("TEC semiannual report", date(2027, 1, 15), "Campaign finance", TEC_FINANCE_HOME),
    Milestone("Bill filing deadline (60th day)", date(2027, 3, 12), "Legislature", TLC_DATES),
    Milestone("Sine die — session ends", date(2027, 5, 31), "Legislature", TLC_DATES),
)

COUNTDOWN_TARGETS = (
    ("General Election", date(2026, 11, 3)),
    ("First Day of Bill Filing", date(2026, 11, 9)),
    ("90th Session Convenes", date(2027, 1, 12)),
    ("Bill Filing Deadline", date(2027, 3, 12)),
    ("Sine Die — Session Ends", date(2027, 5, 31)),
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
          z-index:1000;width:min(1260px,calc(100vw - 28px));margin:0!important;padding:8px;
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
        .priority-stack{background:#f9fafb;border:1px solid #cbd2d9;border-top:3px solid #8e2932;margin-bottom:11px}
        .priority-row{display:grid;grid-template-columns:30px 1fr auto;gap:10px;align-items:start;padding:9px 11px;border-bottom:1px solid #dde2e7}
        .priority-row:last-child{border-bottom:0}.priority-index{font-family:'IBM Plex Mono';font-size:9px;color:#8e2932;padding-top:2px}
        .priority-title{font-family:'Libre Caslon Text',Georgia,serif;font-size:13px;font-weight:700;line-height:1.3;color:#243243}
        .priority-title a{color:inherit;text-decoration:none}.priority-meta{font-family:'IBM Plex Mono';font-size:7px;color:#74808c;text-transform:uppercase;letter-spacing:.06em;margin-top:4px}
        .priority-score{font-family:'IBM Plex Mono';font-size:7px;color:#6d7884;text-transform:uppercase;white-space:nowrap;background:#e8edf1;padding:4px 6px}
        .subboard-label{font-family:'IBM Plex Mono';font-size:8px;letter-spacing:.11em;text-transform:uppercase;color:#697582;margin:8px 0 6px}
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
        .lower-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:17px;align-items:start;margin-top:14px}
        .lower-grid .field-calendar-grid{grid-template-columns:1fr}
        .deadline-board{background:#f9fafb;border:1px solid #cbd2d9;border-top:3px solid #1c3044;padding:5px 14px 8px;margin-bottom:8px}
        .deadline-head{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:#76242c;font-weight:600;padding:8px 0 3px;border-bottom:1px solid #d9dee3}
        .deadline-board .deadline{border-bottom:1px solid #e0e4e8}
        .deadline-board .deadline:last-child{border-bottom:0}
        .agenda{display:grid;gap:12px;margin-top:8px}.agenda-day{background:#f9fafb;border:1px solid #cbd2d9;border-top:3px solid #1c3044}
        .agenda-date{display:flex;justify-content:space-between;align-items:center;padding:9px 12px;background:#edf1f4;border-bottom:1px solid #d3d9df}
        .agenda-date strong{font-family:'Libre Caslon Text',Georgia,serif;font-size:16px;color:#223244}
        .agenda-date span{font-family:'IBM Plex Mono';font-size:8px;text-transform:uppercase;color:#6d7884}
        .agenda-items{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}
        .agenda-item{padding:11px 13px;border-bottom:1px solid #e0e4e8;min-width:0}.agenda-item:nth-child(odd){border-right:1px solid #e0e4e8}
        .agenda-item:only-child{grid-column:1/-1;border-right:0}
        .agenda-time{font-family:'IBM Plex Mono';font-size:8px;color:#8e2932;text-transform:uppercase;letter-spacing:.06em}
        .agenda-title{font-family:'Libre Caslon Text',Georgia,serif;font-size:13px;font-weight:700;color:#263647;line-height:1.3;margin:4px 0}
        .agenda-title a{color:inherit;text-decoration:none}.agenda-meta{font-size:9px;color:#74808c;line-height:1.45}
        .day-header{font-family:'IBM Plex Mono',monospace;font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:#76242c;font-weight:600;margin:15px 0 8px;border-bottom:1px solid #cbd2d9;padding-bottom:4px}
        .chart-panel{background:#f9fafb;border:1px solid #cbd2d9;border-top:3px solid #1c3044;padding:12px 15px;margin:6px 0 14px}
        .chart-title{font-family:'Libre Caslon Text',Georgia,serif;font-size:16px;font-weight:700;color:#1b2939}
        .chart-legend{display:flex;gap:16px;align-items:center;font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:#52514e;margin:6px 0 10px}
        .chart-legend span{display:inline-flex;align-items:center}
        .chart-swatch{width:10px;height:10px;display:inline-block;margin-right:6px;border-radius:2px}
        .chart-swatch.in,.chart-bar.in{background:#1c5cab}
        .chart-swatch.out,.chart-bar.out{background:#b52b35}
        .chart-row{display:grid;grid-template-columns:minmax(140px,230px) 1fr;gap:12px;align-items:center;padding:6px 2px;border-bottom:1px solid #e0e4e8}
        .chart-row:last-child{border-bottom:0}.chart-row:hover{background:#f0f3f5}
        .chart-name{font-size:11px;font-weight:600;color:#283646;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
        .chart-bar-line{display:flex;align-items:center;gap:7px;margin:2px 0}
        .chart-bar{height:11px;border-radius:0 3px 3px 0;min-width:2px;display:inline-block;flex:none}
        .chart-value{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#52514e;white-space:nowrap}
        .briefing-head{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;padding:16px 2px 11px}
        .briefing-date{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.13em;text-transform:uppercase;color:#8e2932;font-weight:600}
        .briefing-title{font-family:'Libre Caslon Text',Georgia,serif;font-size:34px;line-height:1.08;color:#172435;margin-top:4px}
        .briefing-copy{font-size:11px;line-height:1.55;color:#687583;max-width:560px;text-align:right}
        .coverage-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid #c9d0d7;background:#f8fafb;margin:11px 0 12px}
        .coverage-cell{padding:10px 13px;border-right:1px solid #d7dde2;min-width:0}.coverage-cell:last-child{border-right:0}
        .coverage-top{display:flex;align-items:center;justify-content:space-between;gap:8px}
        .coverage-name{font-family:'IBM Plex Mono';font-size:8px;letter-spacing:.1em;text-transform:uppercase;color:#4f5c69;font-weight:600}
        .coverage-state{font-family:'IBM Plex Mono';font-size:7px;text-transform:uppercase;color:#6f7b87;white-space:nowrap}
        .coverage-value{font-family:'Libre Caslon Text';font-size:18px;font-weight:700;color:#213142;margin-top:4px}
        .coverage-note{font-size:8px;color:#798590;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .coverage-state:before{content:'';display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:5px;background:#a3acb7}
        .coverage-state.live:before{background:#16805a}.coverage-state.cached:before{background:#3374aa}
        .coverage-state.stale:before{background:#d48712}.coverage-state.unavailable:before{background:#a3acb7}
        .expanded-coverage{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));border:1px solid #c9d0d7;
          background:#f8fafb;margin:5px 0 15px}.expanded-coverage .coverage-cell:last-child{border-right:0}
        .action-queue{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));border:1px solid #c9d0d7;
          border-top:3px solid #8e2932;background:#f8fafb;margin:7px 0 12px}
        .action-row{display:grid;grid-template-columns:34px minmax(0,1fr) auto;gap:9px;padding:11px 12px;
          border-bottom:1px solid #dde2e7;min-width:0}
        .action-row:nth-child(odd){border-right:1px solid #dde2e7}
        .action-row:nth-last-child(-n+2){border-bottom:0}
        .action-index{font-family:'IBM Plex Mono';font-size:9px;color:#8e2932;padding-top:3px}
        .action-title{font-family:'Libre Caslon Text',Georgia,serif;font-size:13px;font-weight:700;
          line-height:1.3;color:#243243}
        .action-title a{color:inherit;text-decoration:none}.action-title a:hover{text-decoration:underline}
        .action-meta{font-family:'IBM Plex Mono';font-size:7px;color:#74808c;text-transform:uppercase;
          letter-spacing:.04em;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .action-timing{font-family:'IBM Plex Mono';font-size:8px;color:#76242c;background:#f0e7e8;
          padding:5px 6px;white-space:nowrap;height:fit-content}
        a:focus-visible,button:focus-visible{outline:3px solid #d99b31!important;outline-offset:2px}
        .record-title a:hover,.priority-title a:hover,.headline-copy a:hover,.field-title a:hover,.agenda-title a:hover{text-decoration:underline}
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
          .priority-row{grid-template-columns:25px 1fr}.priority-score{display:none}
          .hearing-board{min-height:210px}.publisher-panel{min-height:0}.board-title{font-size:25px}
          .lower-board{height:auto;max-height:410px}.field-row{grid-template-columns:52px 1fr}.field-region{display:none}
          .field-calendar-grid{grid-template-columns:1fr}
          .lower-grid{grid-template-columns:1fr}
          .agenda-items{grid-template-columns:1fr}.agenda-item:nth-child(odd){border-right:0}
          .chart-row{grid-template-columns:1fr;gap:3px}.chart-name{white-space:normal}
          .briefing-head{display:block}.briefing-title{font-size:28px}.briefing-copy{text-align:left;margin-top:7px}
          .coverage-grid{grid-template-columns:repeat(2,1fr)}.coverage-cell:nth-child(2){border-right:0}
          .coverage-cell:nth-child(-n+2){border-bottom:1px solid #d7dde2}
          .expanded-coverage{grid-template-columns:repeat(2,1fr)}
          .expanded-coverage .coverage-cell:nth-child(even){border-right:0}
          .expanded-coverage .coverage-cell{border-bottom:1px solid #d7dde2}
          .action-queue{grid-template-columns:1fr}.action-row:nth-child(odd){border-right:0}
          .action-row:nth-last-child(-n+2){border-bottom:1px solid #dde2e7}.action-row:last-child{border-bottom:0}
          .action-row{grid-template-columns:27px minmax(0,1fr)}.action-timing{grid-column:2;width:max-content}
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


@st.cache_data(ttl=900, show_spinner=False)
def live_government_intelligence() -> list[SourceResult]:
    return fetch_government_intelligence()


@st.cache_data(ttl=3600, show_spinner=False)
def live_influence_intelligence() -> list[SourceResult]:
    return fetch_influence_intelligence()


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


# 30 minutes: six syndication requests per refresh against X's 30-per-window
# anonymous quota leaves headroom for other sessions on the same address.
@st.cache_data(ttl=1800, show_spinner=False)
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
    if not default_handles:
        # Without configured favorites, lead with active leadership accounts rather
        # than the directory's alphabetical head, which is dominated by quiet accounts.
        default_handles = list(DEFAULT_X_HANDLES)
    if directory_items:
        by_handle = {a.handle.lower(): a for a in directory_items}
        ordered = [by_handle[h.lower()] for h in default_handles if h.lower() in by_handle]
        seen = {a.handle.lower() for a in ordered}
        remaining = sorted(
            (account for account in directory_items if account.handle.lower() not in seen),
            key=lambda account: account.handle.lower(),
        )
        if remaining:
            offset = TODAY.toordinal() % len(remaining)
            remaining = remaining[offset:] + remaining[:offset]
        for account in remaining:
            if account.handle.lower() not in seen:
                ordered.append(account)
                seen.add(account.handle.lower())
    else:
        handles = default_handles or list(DEFAULT_X_HANDLES)
        ordered = [
            LegislatorSocialAccount(name=h, chamber="", handle=h, profile_url=f"https://x.com/{h}")
            for h in handles
        ]
    return ordered[:PUBLIC_INDEX_MAX_ACCOUNTS]


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
    topics = "".join(
        f'<span class="tag">{escape(topic)}</span>'
        for topic in extract_topics(f"{item.title} {item.summary}")
    )
    # Aggregator feeds often restate the title as the description — skip those.
    redundant = item.summary.lower().startswith(item.title[:60].lower())
    summary = escape(item.summary[:220]) if item.summary and not redundant else ""
    summary_html = f'<div class="record-summary">{summary}</div>' if summary else ""
    st.markdown(
        f"""<div class="record"><div class="record-top">
        <span class="tag tag-red">{escape(item.publisher)}</span>{topics}</div>
        <div class="record-title"><a href="{safe_url(item.url)}" target="_blank">{escape(item.title)} ↗</a></div>
        {summary_html}
        <div class="meta"><span>{escape(fmt_time(item.published_at))}</span></div></div>""",
        unsafe_allow_html=True,
    )


def government_record_card(item: GovernmentRecord) -> None:
    value = f"<span>{escape(item.value)}</span>" if item.value else ""
    identifier = (
        f'<span class="tag">{escape(item.identifier)}</span>' if item.identifier else ""
    )
    summary = (
        f'<div class="record-summary">{escape(item.summary[:420])}</div>'
        if item.summary
        else ""
    )
    time_parts = []
    if item.published_at:
        published_label = (
            "Registered"
            if item.category == "Lobby registration"
            else "Filed"
            if item.status == "Filed"
            else "Published"
        )
        time_parts.append(
            f"<span>{published_label} {escape(fmt_time(item.published_at))}</span>"
        )
    if item.occurs_at:
        occurs_label = (
            "Meets"
            if item.category == "Open meeting"
            else "Through"
            if item.category == "Lobby registration"
            else "Occurs"
        )
        time_parts.append(
            f"<span>{occurs_label} {escape(fmt_time(item.occurs_at))}</span>"
        )
    if not time_parts:
        time_parts.append("<span>Current official record</span>")
    st.markdown(
        f"""<div class="record"><div class="record-top">
        <span class="tag tag-red">{escape(item.category)}</span>
        <span class="tag tag-blue">{escape(item.status or 'Current')}</span>{identifier}</div>
        <div class="record-title"><a href="{safe_url(item.url)}" target="_blank">
        {escape(item.title)} ↗</a></div>{summary}
        <div class="meta"><span>{escape(item.agency)}</span>
        {''.join(time_parts)}{value}</div></div>""",
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
    """The next three flagship political dates, selected automatically."""
    targets = [(name, target) for name, target in COUNTDOWN_TARGETS if target >= TODAY][:3]
    if not targets:
        targets = list(COUNTDOWN_TARGETS[-3:])
    cards = []
    now = datetime.now(CENTRAL)
    for index, (name, target) in enumerate(targets, 1):
        target_time = datetime.combine(target, datetime.min.time(), tzinfo=CENTRAL)
        if "Convenes" in name:
            target_time = target_time.replace(hour=12)
        remaining = max(0, int((target_time - now).total_seconds()))
        days, hours = remaining // 86400, (remaining % 86400) // 3600
        state = "Complete" if target < TODAY else f"{days}"
        unit = "complete" if target < TODAY else f"days<br>{hours} hrs"
        cards.append(
            f"""<div class="countdown"><span class="countdown-index">0{index}</span>
            <div><div class="countdown-date">{escape(target.strftime('%B %d, %Y'))}</div>
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


def priority_news_panel(headlines: list[Headline], limit: int = 5) -> str:
    """A diverse, action-ranked lead list for the command center."""
    selected: list[Headline] = []
    seen_publishers: set[str] = set()
    for item in headlines:
        if item.publisher in seen_publishers:
            continue
        selected.append(item)
        seen_publishers.add(item.publisher)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for item in headlines:
            if item in selected:
                continue
            selected.append(item)
            if len(selected) >= limit:
                break
    if not selected:
        return '<div class="empty">No priority reporting is available.</div>'
    rows = []
    for index, item in enumerate(selected, 1):
        topics = ", ".join(extract_topics(f"{item.title} {item.summary}")) or "Texas politics"
        score = headline_priority(item)
        signal = "High" if score >= 22 else "Strong" if score >= 16 else "Monitor"
        rows.append(
            f"""<div class="priority-row"><span class="priority-index">0{index}</span><div>
            <div class="priority-title"><a href="{safe_url(item.url)}" target="_blank">{escape(item.title)} ↗</a></div>
            <div class="priority-meta">{escape(item.publisher)} · {escape(compact_age(item.published_at))} ago · {escape(topics)}</div></div>
            <span class="priority-score">{signal} priority</span></div>"""
        )
    return f'<div class="priority-stack">{"".join(rows)}</div>'


def source_coverage_panel(
    hearings: list[SourceResult],
    headlines: list[SourceResult],
    events: list[SourceResult],
    directory: SourceResult,
    posts: SourceResult,
) -> str:
    """Compact provenance and freshness summary for the command center."""
    groups = [
        ("Official legislature", hearings, "Texas Legislature Online"),
        ("Texas reporting", headlines, "Direct + indexed coverage"),
        ("Field calendars", events, "State, county, and clubs"),
        ("Legislator pulse", [directory, posts], "LRL + public X paths"),
    ]
    cells = []
    for name, results, note in groups:
        responding = [result for result in results if result.freshness != "unavailable"]
        records = sum(len(result.items) for result in results)
        if any(result.freshness == "live" for result in results):
            state = "live"
        elif any(result.freshness == "cached" for result in results):
            state = "cached"
        elif any(result.freshness == "stale" for result in results):
            state = "stale"
        else:
            state = "unavailable"
        cells.append(
            f"""<div class="coverage-cell"><div class="coverage-top">
            <span class="coverage-name">{escape(name)}</span>
            <span class="coverage-state {state}">{escape(state)}</span></div>
            <div class="coverage-value">{records} records</div>
            <div class="coverage-note">{len(responding)}/{len(results)} sources · {escape(note)}</div></div>"""
        )
    return f'<div class="coverage-grid">{"".join(cells)}</div>'


def expanded_intelligence_panel(
    government: list[SourceResult], influence: list[SourceResult]
) -> str:
    groups = [
        ("Agency meetings", "Open meeting", government),
        ("Rulemaking", "Rules", government),
        ("Governor & courts", "Executive / judicial", government),
        ("Election data", "Official portals", government),
        ("Lobby & campaign", "Daily disclosures", influence),
        ("State contracts", "Active listings", influence),
    ]
    all_government = flatten(government)
    all_influence = flatten(influence)
    cells = []
    for label, note, results in groups:
        records = all_government if results is government else all_influence
        if label == "Agency meetings":
            count = sum(item.category == "Open meeting" for item in records)
        elif label == "Rulemaking":
            count = sum("Rules" in item.category or item.category == "Regulatory docket" for item in records)
        elif label == "Governor & courts":
            count = sum(
                item.agency in {"Office of the Governor", "Supreme Court of Texas"}
                for item in records
            )
        elif label == "Election data":
            count = sum("Election" in item.category for item in records)
        elif label == "Lobby & campaign":
            count = sum(
                item.category in {"Lobby registration", "Direct campaign expenditure"}
                or item.category == "Lobby activity report"
                for item in records
            )
        else:
            count = sum(item.category == "State contract" for item in records)
        responding = sum(result.freshness != "unavailable" for result in results)
        cells.append(
            f"""<div class="coverage-cell"><div class="coverage-top">
            <span class="coverage-name">{escape(label)}</span>
            <span class="coverage-state {'live' if responding else 'unavailable'}">
            {'live' if responding else 'unavailable'}</span></div>
            <div class="coverage-value">{count} records</div>
            <div class="coverage-note">{escape(note)}</div></div>"""
        )
    return (
        '<div class="subboard-label">Expanded live intelligence</div>'
        f'<div class="expanded-coverage">{"".join(cells)}</div>'
    )


def action_queue_panel(items: list[GovernmentRecord]) -> str:
    """Compact, source-linked queue of official records requiring attention."""
    if not items:
        return '<div class="empty">No official records match the current watchlist.</div>'
    now = datetime.now(CENTRAL)
    rows = []
    for index, item in enumerate(items, 1):
        if item.occurs_at:
            local_occurs = item.occurs_at.astimezone(CENTRAL)
            delta = local_occurs - now
            hours = int(delta.total_seconds() // 3600)
            if delta.total_seconds() < 0:
                timing = "Elapsed"
            elif hours < 24:
                timing = "Today" if local_occurs.date() == now.date() else f"In {hours}h"
            else:
                days = max(1, (local_occurs.date() - now.date()).days)
                timing = f"In {days}d"
        elif item.published_at:
            timing = f"{compact_age(item.published_at)} old"
        else:
            timing = "Current"
        detail = " · ".join(
            part
            for part in [item.category, item.agency, item.status, item.identifier]
            if part
        )
        rows.append(
            f"""<div class="action-row"><span class="action-index">{index:02d}</span><div>
            <div class="action-title"><a href="{safe_url(item.url)}" target="_blank">
            {escape(item.title)} ↗</a></div><div class="action-meta">{escape(detail)}</div></div>
            <span class="action-timing">{escape(timing)}</span></div>"""
        )
    return f'<div class="action-queue">{"".join(rows)}</div>'


def government_records_csv(items: Iterable[GovernmentRecord]) -> bytes:
    """Export normalized official records without exposing internal objects."""
    rows = [
        {
            "title": item.title,
            "category": item.category,
            "agency": item.agency,
            "status": item.status,
            "identifier": item.identifier,
            "published_at": item.published_at.isoformat() if item.published_at else "",
            "occurs_at": item.occurs_at.isoformat() if item.occurs_at else "",
            "value": item.value,
            "summary": item.summary,
            "source_url": item.url,
        }
        for item in items
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "title",
            "category",
            "agency",
            "status",
            "identifier",
            "published_at",
            "occurs_at",
            "value",
            "summary",
            "source_url",
        ],
    ).to_csv(index=False).encode("utf-8-sig")


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


def calendar_agenda(events: list[PoliticalEvent]) -> str:
    """Readable, date-grouped calendar used after filters are applied."""
    if not events:
        return '<div class="empty">No upcoming events match these filters.</div>'
    days: dict[date, list[PoliticalEvent]] = {}
    for item in events:
        if item.starts_at:
            days.setdefault(item.starts_at.astimezone(CENTRAL).date(), []).append(item)
    groups = []
    for day, items in sorted(days.items()):
        entries = []
        for item in items:
            start = item.starts_at.astimezone(CENTRAL)
            location = " · ".join(x for x in [item.venue, item.address] if x)
            entries.append(
                f"""<div class="agenda-item"><div class="agenda-time">{escape(start.strftime('%I:%M %p').lstrip('0'))} CT · {escape(item.event_type)}</div>
                <div class="agenda-title"><a href="{safe_url(item.url)}" target="_blank">{escape(item.title)} ↗</a></div>
                <div class="agenda-meta">{escape(item.organizer)} · {escape(item.region)}
                {(' · ' + escape(location)) if location else ''}</div></div>"""
            )
        groups.append(
            f"""<section class="agenda-day"><div class="agenda-date"><strong>{escape(day.strftime('%A, %B %d').replace(' 0', ' '))}</strong>
            <span>{len(items)} event{'' if len(items) == 1 else 's'}</span></div>
            <div class="agenda-items">{''.join(entries)}</div></section>"""
        )
    return f'<div class="agenda">{"".join(groups)}</div>'


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


def deadline_board(milestones: list[Milestone]) -> str:
    if not milestones:
        return ""
    rows = []
    for item in milestones:
        days, _ = milestone_status(item.occurs_on, TODAY)
        rows.append(
            f"""<div class="deadline"><div><div class="deadline-name">{escape(item.name)}</div>
            <div class="deadline-date">{item.occurs_on.strftime('%b %d, %Y')} · {escape(item.category)}</div></div>
            <div class="deadline-days">{escape(days)}</div></div>"""
        )
    return f'<div class="deadline-board"><div class="deadline-head">Key deadlines</div>{"".join(rows)}</div>'


def pulse_board(posts: SourceResult, accounts: list[LegislatorSocialAccount]) -> str:
    if posts.items:
        body = social_posts_panel(posts.items)
    elif accounts:
        reason = posts.error or "Live public timelines are temporarily unavailable."
        body = (
            f'<div class="x-roster-note">{escape(reason)} '
            "Showing the official legislator roster meanwhile.</div>"
            + social_directory_panel(accounts)
        )
    else:
        body = '<div class="empty">The legislator directory is temporarily unavailable.</div>'
    return f'<div class="lower-board">{body}</div>'


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
    period = "Morning" if datetime.now(CENTRAL).hour < 12 else "Afternoon"
    st.markdown(
        f"""<div class="briefing-head"><div><div class="briefing-date">
        {escape(TODAY.strftime('%A · %B %d, %Y').replace(' 0', ' '))}</div>
        <div class="briefing-title">{period} intelligence brief</div></div>
        <div class="briefing-copy">Official legislative records, attributed reporting, field calendars,
        and public legislator posts — ranked for fast review and linked to the source record.</div></div>""",
        unsafe_allow_html=True,
    )
    if st.button("Refresh intelligence", help="Clear source caches and request current public data"):
        st.cache_data.clear()
        st.rerun()
    token = configured_x_token()
    base_url = str(get_secret("X_API_BASE_URL", "https://api.x.com") or "https://api.x.com")
    with ThreadPoolExecutor(max_workers=7) as executor:
        hearing_future = executor.submit(live_hearings)
        headline_future = executor.submit(live_headlines)
        event_future = executor.submit(live_events)
        directory_future = executor.submit(live_directory)
        government_future = executor.submit(live_government_intelligence)
        influence_future = executor.submit(live_influence_intelligence)
        directory = directory_future.result()
        accounts = public_feed_accounts(directory.items)
        # With API access, one list request covers every legislator; the anonymous
        # syndication feed is the fallback and the only path without a token.
        if token:
            posts_future = executor.submit(live_x_list_posts, token, base_url)
        else:
            posts_future = executor.submit(live_public_posts, tuple(accounts))
        hearing_results = hearing_future.result()
        headline_results = headline_future.result()
        event_results = event_future.result()
        government_results = government_future.result()
        influence_results = influence_future.result()
        posts = posts_future.result()
    if token and not posts.items:
        posts = live_public_posts(tuple(accounts))
    remember(hearing_results)
    remember(headline_results)
    remember(event_results)
    remember(government_results)
    remember(influence_results)
    remember(directory)
    remember(posts)
    hearings = flatten(hearing_results)
    headlines = dedupe_headlines(headline_results)
    events = dedupe_events(event_results)
    government_records: list[GovernmentRecord] = flatten(government_results)
    influence_records: list[GovernmentRecord] = flatten(influence_results)
    all_results = [
        *hearing_results,
        *headline_results,
        *event_results,
        *government_results,
        *influence_results,
        directory,
        posts,
    ]
    responding = sum(1 for result in all_results if result.freshness != "unavailable")
    st.markdown(
        source_coverage_panel(
            hearing_results, headline_results, event_results, directory, posts
        ),
        unsafe_allow_html=True,
    )
    countdown_strip()
    operations_strip(hearings, headlines, events)
    st.markdown(
        expanded_intelligence_panel(government_results, influence_results),
        unsafe_allow_html=True,
    )
    watch_query = st.text_input(
        "Focus watchlist",
        placeholder="Water, school finance, grid reliability, property tax…",
        help=(
            "Enter comma-separated issues, agencies, organizations, docket numbers, "
            "vendors, or people. The official action queue and downloaded brief will "
            "focus on matching records."
        ),
        key="command_watchlist",
    )
    watch_terms = tuple(
        term.strip() for term in watch_query.split(",") if term.strip()
    )
    action_records = select_action_records(
        [*government_records, *influence_records],
        now=datetime.now(CENTRAL),
        limit=8,
        keywords=watch_terms,
    )
    queue_note = (
        f"Prioritized official records matching {', '.join(watch_terms)}."
        if watch_terms
        else "Prioritized by timing, recency, record status, and source significance."
    )
    st.markdown(
        f'<div class="board-kicker">Official action queue</div>'
        f'<div class="section-note">{escape(queue_note)}</div>'
        f"{action_queue_panel(action_records)}",
        unsafe_allow_html=True,
    )
    if action_records:
        st.download_button(
            "Download action queue (.csv)",
            government_records_csv(action_records),
            f"texas-action-queue-{TODAY.isoformat()}.csv",
            "text/csv",
        )
    st.markdown(
        f"""<div class="intelligence-grid"><section><div class="board-kicker">Texas Legislature</div>
        <div class="board-title">This Week’s Hearings</div><div class="board-rule"></div>
        {hearing_board("House", hearings)}{hearing_board("Senate", hearings)}
        {deadline_board(next_milestones(MILESTONES, TODAY, 4))}</section>
        <section><div class="board-kicker">Across Texas</div>
        <div class="board-title">Priority News Brief</div><div class="board-rule"></div>
        {priority_news_panel(headlines)}
        <div class="subboard-label">Reporting by source</div>{publisher_panels(headlines)}</section></div>
        <div class="board-footer"><span class="connected">{responding}/{len(all_results)} sources responding</span>
        <span>Hearing data: Texas Legislature Online · News updates every 15 minutes</span>
        <span>Updated {datetime.now(CENTRAL).strftime('%I:%M %p').lstrip('0')} CT</span></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class="lower-grid"><section><div class="board-kicker">Republican Field Network</div>
        <div class="board-title">Upcoming GOP Events</div><div class="board-rule"></div>
        <div class="lower-board">{field_calendar_panel(events)}</div></section>
        <section><div class="board-kicker">Legislators on X</div>
        <div class="board-title">Legislator Pulse</div><div class="board-rule"></div>
        {pulse_board(posts, accounts)}</section></div>""",
        unsafe_allow_html=True,
    )
    calendar_col, brief_col = st.columns(2)
    with calendar_col:
        st.download_button(
            "Download GOP calendar (.ics)",
            make_ics(events, "Texas Republican field calendar"),
            "texas-gop-calendar.ics",
            "text/calendar",
            width="stretch",
        )
    with brief_col:
        st.download_button(
            "Download daily brief (.md)",
            make_briefing(
                TODAY,
                hearings,
                headlines,
                events,
                MILESTONES,
                government_records,
                influence_records,
                watch_terms,
            ),
            f"texas-daily-brief-{TODAY.isoformat()}.md",
            "text/markdown",
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
        if not activity:
            prefiling_days = (date(2026, 11, 9) - TODAY).days
            if prefiling_days > 0:
                st.markdown(
                    f'<div class="warning-box"><b>Interim period:</b> Texas Legislature Online '
                    "publishes filing feeds only on days with new activity. Bill prefiling for the "
                    f"90th Legislature opens November 9, 2026 — {prefiling_days} days away.</div>",
                    unsafe_allow_html=True,
                )
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
            dated = sorted(
                (x for x in shown_hearings if x.starts_at),
                key=lambda x: x.starts_at,
            )
            current_day: date | None = None
            for item in dated:
                item_day = item.starts_at.astimezone(CENTRAL).date()
                if item_day != current_day:
                    current_day = item_day
                    st.markdown(
                        f'<div class="day-header">{item_day.strftime("%A, %B %d")}</div>',
                        unsafe_allow_html=True,
                    )
                hearing_card(item)
            undated = [x for x in shown_hearings if not x.starts_at]
            if undated:
                st.markdown('<div class="day-header">Date pending</div>', unsafe_allow_html=True)
                for item in undated:
                    hearing_card(item)
        else:
            empty_state("No hearing notices match these filters.")

    with tab_dates:
        for milestone in sorted(MILESTONES, key=lambda m: m.occurs_on):
            milestone_row(milestone)
        st.link_button("Texas Legislative Council dates ↗", TLC_DATES)


def money_label(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:,.0f}K"
    return f"${value:,.0f}"


def finance_chart(items: list[FinanceSummary]) -> str:
    """Paired-bar comparison of the top filers by contributions.

    Colors validated for CVD safety and contrast: #1c5cab (contributions) and
    #b52b35 (expenditures); values are also direct-labeled in ink.
    """
    top = sorted(items, key=lambda x: x.contributions, reverse=True)[:10]
    if not top:
        return ""
    scale = max(max(x.contributions for x in top), max(x.expenditures for x in top), 1.0)
    rows = []
    for item in top:
        # 78% ceiling leaves room for the direct value label beside the longest bar.
        in_width = max(0.6, item.contributions / scale * 78)
        out_width = max(0.6, item.expenditures / scale * 78)
        rows.append(
            f"""<div class="chart-row"><div class="chart-name" title="{escape(item.filer_name)}">{escape(item.filer_name)}</div>
            <div><div class="chart-bar-line"><span class="chart-bar in" style="width:{in_width:.1f}%"></span>
            <span class="chart-value">{escape(money_label(item.contributions))}</span></div>
            <div class="chart-bar-line"><span class="chart-bar out" style="width:{out_width:.1f}%"></span>
            <span class="chart-value">{escape(money_label(item.expenditures))}</span></div></div></div>"""
        )
    return f"""<div class="chart-panel"><div class="chart-title">Top 10 PACs by contributions</div>
    <div class="chart-legend"><span><span class="chart-swatch in"></span>Contributions</span>
    <span><span class="chart-swatch out"></span>Expenditures</span></div>{"".join(rows)}</div>"""


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
    if shown:
        st.markdown(finance_chart(shown), unsafe_allow_html=True)
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
    st.download_button(
        "Download filtered results (.csv)",
        frame.to_csv(index=False),
        "tec-pac-summary.csv",
        "text/csv",
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
    now = datetime.now(CENTRAL)
    fresh_24 = sum(
        1 for x in items if x.published_at and x.published_at >= now - timedelta(hours=24)
    )
    summary_cols = st.columns(4)
    with summary_cols[0]:
        metric("Headlines", str(len(items)), "deduplicated, 7-day window")
    with summary_cols[1]:
        metric("Past 24 hours", str(fresh_24), "new attributed reporting")
    with summary_cols[2]:
        metric("Publishers", str(len({x.publisher for x in items})), "named outlets")
    with summary_cols[3]:
        metric(
            "Sources online",
            f"{sum(1 for x in results if x.freshness != 'unavailable')}/{len(results)}",
            "responding now",
        )
    publishers = ["All"] + sorted({x.publisher for x in items})
    topics = sorted(
        {
            topic
            for item in items
            for topic in extract_topics(f"{item.title} {item.summary}")
        }
    )
    c1, c2, c3, c4, c5 = st.columns([1.25, 1, 1, 1.15, 1.8])
    with c1:
        publisher = st.selectbox("Publisher", publishers)
    with c2:
        window = st.selectbox("Window", ["Any time", "24 hours", "3 days", "7 days"])
    with c3:
        issue = st.selectbox("Issue", ["All issues"] + topics)
    with c4:
        ordering = st.selectbox("Sort", ["Priority brief", "Newest first"])
    with c5:
        query = st.text_input("Search headlines", placeholder="Issue, official, county, or race")
    window_hours = {"24 hours": 24, "3 days": 72, "7 days": 168}.get(window)
    cutoff = now - timedelta(hours=window_hours) if window_hours else None
    shown = [
        x for x in items
        if (publisher == "All" or x.publisher == publisher)
        and (
            issue == "All issues"
            or issue in extract_topics(f"{x.title} {x.summary}")
        )
        and (not query or query.lower() in f"{x.title} {x.summary}".lower())
        and (cutoff is None or (x.published_at and x.published_at >= cutoff))
    ]
    if ordering == "Priority brief":
        shown = sorted(
            shown,
            key=lambda x: (
                headline_priority(x),
                x.relevance,
                x.published_at.timestamp() if x.published_at else 0,
            ),
            reverse=True,
        )
    else:
        shown = sorted(
            shown,
            key=lambda x: x.published_at.timestamp() if x.published_at else 0,
            reverse=True,
        )
    if shown:
        for item in shown[:50]:
            headline_card(item)
    else:
        empty_state("No headlines match these filters.")
    with st.expander("News sources and freshness"):
        st.caption(
            "Direct publisher feeds are supplemented by tightly scoped seven-day indexes. "
            "Every headline retains its named publisher and source link."
        )
        for result in sorted(results, key=lambda x: x.source_name):
            status_line(result)


def social_page() -> None:
    st.markdown("## Texas legislators on X")
    st.caption(
        "Works without a token by combining public X timelines with recently indexed public posts. "
        "Account identities come from the Legislative Reference Library."
    )
    with st.expander("Optional: connect X API access"):
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
            if st.button("Use no-token public feed", width="stretch"):
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
    token = configured_x_token()
    defaults = get_secret(
        "SOCIAL_DEFAULT_HANDLES",
        get_secret("TXLEGE_X_HANDLES", []),
    )
    default_handles = {str(x).lstrip("@").lower() for x in defaults} if isinstance(defaults, list) else set()
    if not default_handles:
        default_handles = {handle.lower() for handle in DEFAULT_X_HANDLES}
    default_accounts = [x for x in filtered if x.handle.lower() in default_handles][:10]
    if not default_accounts:
        default_accounts = filtered[:PUBLIC_FEED_MAX_ACCOUNTS]
    if not token:
        default_accounts = public_feed_accounts(filtered)
    options = {f"{x.name} · @{x.handle}": x for x in filtered}
    default_labels = [
        label for label, account in options.items() if account in default_accounts
    ]
    account_limit = 10 if token else PUBLIC_INDEX_MAX_ACCOUNTS
    selected_labels = st.multiselect(
        f"Tracked accounts (maximum {account_limit})",
        list(options),
        default=default_labels,
        max_selections=account_limit,
    )
    selected = [options[label] for label in selected_labels] or default_accounts
    base_url = str(get_secret("X_API_BASE_URL", "https://api.x.com") or "https://api.x.com")
    tab_feed, tab_directory = st.tabs(["Recent posts", f"Directory ({len(filtered)})"])
    with tab_feed:
        if token:
            posts = live_posts(token, base_url, tuple(selected))
            if not posts.items:
                posts = live_public_posts(tuple(selected))
        else:
            posts = live_public_posts(tuple(selected))
        remember(posts)
        if posts.items:
            status_line(posts)
            if not token:
                st.caption(
                    f"No token required: direct public timelines cover up to {PUBLIC_FEED_MAX_ACCOUNTS} "
                    f"accounts and public indexes broaden coverage to {PUBLIC_INDEX_MAX_ACCOUNTS}. "
                    "Results refresh every 30 minutes."
                )
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
            detail = escape(posts.error or "No recent posts were returned.")
            st.markdown(
                f'<div class="warning-box"><b>The live feed is temporarily unavailable.</b> {detail} '
                "Recent posts are kept and shown once a successful refresh has happened.</div>",
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
            f"{sum(1 for x in results if x.freshness != 'unavailable')}/{len(results)}",
            "responding now",
        )
    c1, c2, c3, c4, c5 = st.columns([1, 1, 1.35, 1, 1.65])
    with c1:
        region = st.selectbox("Region", ["All"] + sorted({x.region for x in events}))
    with c2:
        event_type = st.selectbox("Event type", ["All"] + sorted({x.event_type for x in events}))
    with c3:
        organizer = st.selectbox("Organization", ["All"] + organizations)
    with c4:
        window = st.selectbox(
            "Window",
            ["Next 30 days", "Next 14 days", "Next 60 days", "Next 120 days"],
        )
    with c5:
        query = st.text_input("Search events", placeholder="Club, organizer, venue, or keyword")
    horizon = {
        "Next 14 days": 14,
        "Next 30 days": 30,
        "Next 60 days": 60,
        "Next 120 days": 120,
    }[window]
    shown = [
        x for x in events
        if (region == "All" or x.region == region)
        and (event_type == "All" or x.event_type == event_type)
        and (organizer == "All" or x.organizer == organizer)
        and (not query or query.lower() in f"{x.title} {x.organizer} {x.venue}".lower())
        and x.starts_at
        and x.starts_at.astimezone(CENTRAL).date() >= TODAY
        and x.starts_at.astimezone(CENTRAL).date() <= TODAY + timedelta(days=horizon)
    ]
    if shown:
        st.download_button(
            "Download filtered calendar (.ics)",
            make_ics(shown, "Texas Republican field calendar"),
            "texas-republican-field-calendar.ics",
            "text/calendar",
        )
        st.markdown(calendar_agenda(shown), unsafe_allow_html=True)
    else:
        empty_state("No upcoming events match these filters.")
    with st.expander("Connected sources and status"):
        st.caption(
            "A live source with zero records is online but has no dated upcoming events. "
            "Open any source to confirm undated notices or late calendar changes."
        )
        for result in sorted(results, key=lambda x: x.source_name):
            status_line(result)
            st.link_button(f"Open {result.source_name} ↗", result.source_url)


def government_page() -> None:
    st.markdown("## Government activity")
    st.caption(
        "Official agency meetings, Texas Register rules, governor actions, regulatory matters, "
        "court orders, election data, and legislative vote surfaces."
    )
    results = live_government_intelligence()
    remember(results)
    records: list[GovernmentRecord] = flatten(results)
    now = datetime.now(CENTRAL)
    upcoming = sum(1 for item in records if item.occurs_at and item.occurs_at >= now)
    summary_cols = st.columns(4)
    with summary_cols[0]:
        metric("Government records", f"{len(records):,}", "normalized live records")
    with summary_cols[1]:
        metric("Upcoming meetings", f"{upcoming:,}", "state and regional agencies")
    with summary_cols[2]:
        metric("Agencies", f"{len({x.agency for x in records}):,}", "named authorities")
    with summary_cols[3]:
        metric(
            "Sources online",
            f"{sum(x.freshness != 'unavailable' for x in results)}/{len(results)}",
            "responding now",
        )
    categories = sorted({item.category for item in records})
    agencies = sorted({item.agency for item in records})
    c1, c2, c3, c4 = st.columns([1.25, 1.35, 1, 2])
    with c1:
        category = st.selectbox(
            "Record type", ["All"] + categories, key="government_category"
        )
    with c2:
        agency = st.selectbox("Agency", ["All"] + agencies, key="government_agency")
    with c3:
        timing = st.selectbox(
            "Timing",
            ["Current and upcoming", "Upcoming only", "Past 30 days", "All"],
            key="government_timing",
        )
    with c4:
        query = st.text_input(
            "Search government records",
            placeholder="Agency, docket, rule, meeting, case, or issue",
            key="government_query",
        )
    cutoff = now - timedelta(days=30)
    shown: list[GovernmentRecord] = []
    for item in records:
        haystack = (
            f"{item.title} {item.summary} {item.agency} {item.identifier} "
            f"{item.category} {item.status}"
        ).lower()
        timing_match = (
            timing == "All"
            or (
                timing == "Upcoming only"
                and item.occurs_at is not None
                and item.occurs_at >= now
            )
            or (
                timing == "Past 30 days"
                and item.published_at is not None
                and item.published_at >= cutoff
            )
            or (
                timing == "Current and upcoming"
                and (
                    item.occurs_at is None
                    or item.occurs_at >= now
                    or (item.published_at is not None and item.published_at >= cutoff)
                )
            )
        )
        if (
            (category == "All" or item.category == category)
            and (agency == "All" or item.agency == agency)
            and (not query or query.lower() in haystack)
            and timing_match
        ):
            shown.append(item)
    shown.sort(
        key=lambda item: (
            0 if item.occurs_at and item.occurs_at >= now else 1,
            item.occurs_at.timestamp()
            if item.occurs_at and item.occurs_at >= now
            else -(item.published_at.timestamp() if item.published_at else 0),
        )
    )
    st.caption(f"Showing {min(len(shown), 100):,} of {len(shown):,} matching records.")
    if shown:
        export_col, calendar_col = st.columns(2)
        with export_col:
            st.download_button(
                "Download matching records (.csv)",
                government_records_csv(shown),
                f"texas-government-records-{TODAY.isoformat()}.csv",
                "text/csv",
                width="stretch",
            )
        with calendar_col:
            calendar_records = [
                item for item in shown if item.occurs_at and item.occurs_at >= now
            ]
            st.download_button(
                "Add upcoming dates to calendar (.ics)",
                make_ics(calendar_records, "Texas government meetings and deadlines"),
                f"texas-government-calendar-{TODAY.isoformat()}.ics",
                "text/calendar",
                width="stretch",
                disabled=not calendar_records,
            )
        for item in shown[:100]:
            government_record_card(item)
    else:
        empty_state("No government records match these filters.")
    with st.expander("Government sources and freshness"):
        for result in sorted(results, key=lambda item: item.source_name):
            status_line(result)
            st.link_button(f"Open {result.source_name} ↗", result.source_url)


def influence_page() -> None:
    st.markdown("## Influence, disclosures, and contracts")
    st.caption(
        "Current TEC direct expenditures, daily lobby registrations, filed lobby-activity "
        "reports and compensation bands, and active Comptroller contract listings."
    )
    results = live_influence_intelligence()
    remember(results)
    records: list[GovernmentRecord] = flatten(results)
    categories = sorted({item.category for item in records})
    active_lobby = sum(
        item.category == "Lobby registration" and item.status == "Active"
        for item in records
    )
    summary_cols = st.columns(4)
    with summary_cols[0]:
        metric("Disclosure records", f"{len(records):,}", "current normalized set")
    with summary_cols[1]:
        metric("Active lobby records", f"{active_lobby:,}", "client–lobbyist relationships")
    with summary_cols[2]:
        metric(
            "Direct expenditures",
            f"{sum(item.category == 'Direct campaign expenditure' for item in records):,}",
            "current-year reports",
        )
    with summary_cols[3]:
        metric(
            "Public contracts",
            f"{sum(item.category == 'State contract' for item in records):,}",
            "active Comptroller listings",
        )
    c1, c2, c3 = st.columns([1.2, 1.2, 2.2])
    with c1:
        category = st.selectbox(
            "Disclosure type", ["All"] + categories, key="influence_category"
        )
    with c2:
        state = st.selectbox(
            "Status",
            ["All", "Active", "Filed", "Active listing", "Terminated"],
            key="influence_status",
        )
    with c3:
        query = st.text_input(
            "Search disclosures",
            placeholder="Client, lobbyist, filer, vendor, purpose, or report ID",
            key="influence_query",
        )
    shown = [
        item
        for item in records
        if (category == "All" or item.category == category)
        and (state == "All" or item.status == state)
        and (
            not query
            or query.lower()
            in f"{item.title} {item.summary} {item.identifier} {item.value}".lower()
        )
    ]
    shown.sort(
        key=lambda item: (
            item.published_at.timestamp() if item.published_at else 0,
            item.title.lower(),
        ),
        reverse=True,
    )
    st.caption(f"Showing {min(len(shown), 100):,} of {len(shown):,} matching records.")
    if shown:
        st.download_button(
            "Download matching disclosures (.csv)",
            government_records_csv(shown),
            f"texas-influence-records-{TODAY.isoformat()}.csv",
            "text/csv",
        )
        for item in shown[:100]:
            government_record_card(item)
    else:
        empty_state("No disclosure records match these filters.")
    st.info(
        "Lobby compensation is displayed as the range disclosed to TEC. "
        "Direct expenditures use TEC's current-year report; the complete transaction archive "
        "remains linked at the source because it is approximately 1 GB."
    )
    with st.expander("Disclosure sources and freshness"):
        for result in sorted(results, key=lambda item: item.source_name):
            status_line(result)
            st.link_button(f"Open {result.source_name} ↗", result.source_url)


def source_health_page() -> None:
    st.markdown("## Source health")
    st.caption("Runtime status for authoritative and intelligence feeds checked during this session.")
    if st.button("Check every source now", type="primary"):
        with st.spinner("Checking sources…"):
            activity, hearings, headlines, events, finance = load_command_data()
            directory = live_directory()
            government = live_government_intelligence()
            influence = live_influence_intelligence()
            token = configured_x_token()
            base_url = str(
                get_secret("X_API_BASE_URL", "https://api.x.com") or "https://api.x.com"
            )
            accounts = public_feed_accounts(directory.items)
            posts = (
                live_x_list_posts(token, base_url)
                if token
                else live_public_posts(tuple(accounts))
            )
            if token and not posts.items:
                posts = live_public_posts(tuple(accounts))
            for group in (
                activity,
                hearings,
                headlines,
                events,
                government,
                influence,
            ):
                remember(group)
            remember(finance)
            remember(directory)
            remember(posts)
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

pages = [
    "Command center",
    "Legislature",
    "Campaign finance",
    "Influence",
    "Government",
    "Media",
    "Legislators on X",
    "GOP calendar",
    "Source health",
]
if st.session_state.get("bottom_navigation") not in pages:
    st.session_state["bottom_navigation"] = "Command center"
page = st.session_state["bottom_navigation"]

if page == "Command center":
    command_center()
elif page == "Legislature":
    legislature_page()
elif page == "Campaign finance":
    finance_page()
elif page == "Influence":
    influence_page()
elif page == "Government":
    government_page()
elif page == "Media":
    headlines_page()
elif page == "Legislators on X":
    social_page()
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
