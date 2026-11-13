from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from pge_outage_notifier.domain.errors import InvalidValue

_CITY_SYM_PATTERN = re.compile(r"^\d{7}$")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidValue(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CitySym:
    """Locality identifier in the Polish TERYT (SIMC) register, 7 digits."""

    value: str

    def __post_init__(self) -> None:
        if not _CITY_SYM_PATTERN.match(self.value):
            raise InvalidValue(f"Invalid citySym: {self.value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class OutageId:
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise InvalidValue("OutageId must not be empty")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class TimeWindow:
    start: datetime
    stop: datetime

    def __post_init__(self) -> None:
        _require_aware(self.start, "start")
        _require_aware(self.stop, "stop")
        if self.start >= self.stop:
            raise InvalidValue(f"Window {self.start} - {self.stop} starts after it ends")

    def has_ended(self, now: datetime) -> bool:
        return self.stop <= now

    def has_started(self, now: datetime) -> bool:
        return self.start <= now

    def is_active(self, now: datetime) -> bool:
        return self.has_started(now) and not self.has_ended(now)


@dataclass(frozen=True, slots=True)
class AffectedAddress:
    city_name: str
    street_name: str | None = None
    numbers: tuple[str, ...] | None = None
    """None means the whole locality / the whole street."""

    def describe(self) -> str:
        parts = [self.city_name]
        if self.street_name:
            parts.append(self.street_name)
        if self.numbers:
            parts.append(", ".join(self.numbers))
        return " ".join(parts)


class OutageKind(Enum):
    PLANNED = "planned"
    UNPLANNED = "unplanned"


@dataclass(frozen=True, slots=True)
class OutageDetails:
    """One outage exactly as the source described it when the snapshot was taken."""

    outage_id: OutageId
    description: str
    windows: tuple[TimeWindow, ...]
    addresses: tuple[AffectedAddress, ...]
    kind: OutageKind = OutageKind.PLANNED
    revoked: bool = False
    revoked_description: str | None = None
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.windows:
            raise InvalidValue(f"Outage {self.outage_id} has no time window")
        object.__setattr__(self, "windows", tuple(sorted(self.windows)))


@dataclass(frozen=True, slots=True)
class OutageSnapshot:
    """State of outages for one locality at `taken_at`."""

    city_sym: CitySym
    taken_at: datetime
    outages: tuple[OutageDetails, ...]

    def __post_init__(self) -> None:
        _require_aware(self.taken_at, "taken_at")
        ids = [o.outage_id for o in self.outages]
        if len(ids) != len(set(ids)):
            raise InvalidValue("Snapshot contains duplicate outage identifiers")


class ReminderKind(Enum):
    EVENING_BEFORE = "evening_before"
    SHORTLY_BEFORE = "shortly_before"


@dataclass(frozen=True, slots=True)
class ReminderPolicy:
    """When to remind about an upcoming outage window."""

    timezone: ZoneInfo
    evening_before_hour: int = 18
    shortly_before: timedelta = timedelta(minutes=60)

    def __post_init__(self) -> None:
        if not 0 <= self.evening_before_hour <= 23:
            raise InvalidValue("evening_before_hour must be within 0-23")
        if self.shortly_before <= timedelta(0):
            raise InvalidValue("shortly_before must be positive")

    def reminder_time(self, kind: ReminderKind, window: TimeWindow) -> datetime:
        local_start = window.start.astimezone(self.timezone)
        match kind:
            case ReminderKind.EVENING_BEFORE:
                day_before = local_start.date() - timedelta(days=1)
                return datetime.combine(
                    day_before, time(hour=self.evening_before_hour), tzinfo=self.timezone
                )
            case ReminderKind.SHORTLY_BEFORE:
                return local_start - self.shortly_before


@dataclass(frozen=True, slots=True)
class ReminderKey:
    kind: ReminderKind
    window_start: datetime


@dataclass(frozen=True, slots=True)
class ReminderDue:
    outage_id: OutageId
    kind: ReminderKind
    window: TimeWindow
    description: str
    addresses: tuple[AffectedAddress, ...]

    @property
    def key(self) -> ReminderKey:
        return ReminderKey(self.kind, self.window.start)
