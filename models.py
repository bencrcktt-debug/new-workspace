from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Generic, Iterable, Literal, TypeVar


Freshness = Literal["live", "cached", "stale", "unavailable"]
T = TypeVar("T")


@dataclass(frozen=True)
class LegislativeItem:
    title: str
    activity_type: str
    chamber: str
    summary: str
    url: str
    published_at: datetime | None = None


@dataclass(frozen=True)
class Hearing:
    title: str
    chamber: str
    committee: str
    location: str
    url: str
    starts_at: datetime | None = None
    summary: str = ""


@dataclass(frozen=True)
class FinanceSummary:
    rank: int
    filer_name: str
    filer_type: str
    filer_id: str
    city: str
    state: str
    contributions: float
    expenditures: float
    reporting_period: str
    source_url: str

    @property
    def net_activity(self) -> float:
        return self.contributions - self.expenditures


@dataclass(frozen=True)
class Headline:
    title: str
    publisher: str
    url: str
    published_at: datetime | None
    summary: str = ""
    relevance: int = 0


@dataclass(frozen=True)
class LegislatorSocialAccount:
    name: str
    chamber: str
    handle: str
    profile_url: str


@dataclass(frozen=True)
class SocialPost:
    legislator_name: str
    handle: str
    text: str
    url: str
    created_at: datetime | None
    likes: int = 0
    reposts: int = 0


@dataclass(frozen=True)
class PoliticalEvent:
    title: str
    region: str
    organizer: str
    event_type: str
    url: str
    starts_at: datetime | None
    ends_at: datetime | None = None
    venue: str = ""
    address: str = ""


@dataclass(frozen=True)
class Milestone:
    name: str
    occurs_on: date
    category: str
    source_url: str


def next_milestones(
    milestones: Iterable[Milestone], today: date, count: int = 4
) -> list[Milestone]:
    upcoming = [item for item in milestones if item.occurs_on >= today]
    upcoming.sort(key=lambda item: item.occurs_on)
    return upcoming[:count]


def milestone_status(occurs_on: date, today: date) -> tuple[str, int]:
    days = (occurs_on - today).days
    if days < 0:
        return "Complete", days
    if days == 0:
        return "Today", days
    return f"{days} {'day' if days == 1 else 'days'}", days


@dataclass
class SourceResult(Generic[T]):
    source_name: str
    source_url: str
    items: list[T] = field(default_factory=list)
    fetched_at: datetime | None = None
    freshness: Freshness = "unavailable"
    latency_ms: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.freshness in {"live", "cached", "stale"} and bool(self.items)
