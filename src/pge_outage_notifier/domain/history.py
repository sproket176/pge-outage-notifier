"""Outage ledger: a permanent, never-pruned record of what PGE published and how it changed.

Used to settle the yearly allowances for supply interruptions, separately for planned
outages and for failures. It stores the times declared by PGE, not measured downtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from pge_outage_notifier.domain.errors import DomainError, InvalidValue
from pge_outage_notifier.domain.values import (
    AffectedAddress,
    CitySym,
    OutageDetails,
    OutageId,
    OutageKind,
    TimeWindow,
)


class RecordStatus(Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    WITHDRAWN = "withdrawn"


class ChangeKind(Enum):
    OBSERVED = "observed"
    RESCHEDULED = "rescheduled"
    REVOKED = "revoked"
    REINSTATED = "reinstated"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    recorded_at: datetime
    kind: ChangeKind
    windows: tuple[TimeWindow, ...]
    note: str | None = None


class OutageRecord:
    """Ledger aggregate: one outage and the full history of its changes."""

    def __init__(
        self,
        outage_id: OutageId,
        city_sym: CitySym,
        kind: OutageKind,
        description: str,
        addresses: tuple[AffectedAddress, ...],
        windows: tuple[TimeWindow, ...],
        status: RecordStatus,
        first_seen_at: datetime,
        last_seen_at: datetime,
        published_at: datetime | None,
        entries: list[HistoryEntry],
    ) -> None:
        if not windows:
            raise DomainError(f"Record {outage_id} must have at least one window")
        if not entries:
            raise DomainError(f"Record {outage_id} must have a history entry")
        self.outage_id = outage_id
        self.city_sym = city_sym
        self.kind = kind
        self.description = description
        self.addresses = addresses
        self.windows = tuple(sorted(windows))
        self.status = status
        self.first_seen_at = first_seen_at
        self.last_seen_at = last_seen_at
        self.published_at = published_at
        self._entries = list(entries)

    @property
    def entries(self) -> tuple[HistoryEntry, ...]:
        return tuple(self._entries)

    @classmethod
    def observe_new(cls, city_sym: CitySym, details: OutageDetails, now: datetime) -> OutageRecord:
        status = RecordStatus.REVOKED if details.revoked else RecordStatus.ACTIVE
        return cls(
            outage_id=details.outage_id,
            city_sym=city_sym,
            kind=details.kind,
            description=details.description,
            addresses=details.addresses,
            windows=details.windows,
            status=status,
            first_seen_at=now,
            last_seen_at=now,
            published_at=details.published_at,
            entries=[
                HistoryEntry(now, ChangeKind.OBSERVED, details.windows, details.revoked_description)
            ],
        )

    def observe(self, details: OutageDetails, now: datetime) -> bool:
        """Records another observation from the source. Returns True when something changed."""
        self.last_seen_at = now
        self.description = details.description
        self.addresses = details.addresses
        if details.published_at is not None:
            self.published_at = details.published_at
        changed = False

        if details.revoked and self.status is not RecordStatus.REVOKED:
            self.status = RecordStatus.REVOKED
            self._entries.append(
                HistoryEntry(now, ChangeKind.REVOKED, details.windows, details.revoked_description)
            )
            changed = True
        elif not details.revoked and self.status is not RecordStatus.ACTIVE:
            self.status = RecordStatus.ACTIVE
            self._entries.append(HistoryEntry(now, ChangeKind.REINSTATED, details.windows))
            changed = True
        elif details.windows != self.windows:
            self._entries.append(HistoryEntry(now, ChangeKind.RESCHEDULED, details.windows))
            changed = True

        self.windows = details.windows
        return changed

    def mark_withdrawn(self, now: datetime) -> bool:
        if self.status is not RecordStatus.ACTIVE:
            return False
        self.status = RecordStatus.WITHDRAWN
        self.last_seen_at = now
        self._entries.append(HistoryEntry(now, ChangeKind.WITHDRAWN, self.windows))
        return True

    @property
    def withdrawn_at(self) -> datetime | None:
        for entry in reversed(self._entries):
            if entry.kind is ChangeKind.WITHDRAWN:
                return entry.recorded_at
        return None

    @property
    def span(self) -> TimeWindow:
        return TimeWindow(self.windows[0].start, max(w.stop for w in self.windows))

    def counted_windows(self) -> tuple[TimeWindow, ...]:
        """Windows that count towards the yearly allowance.

        Planned: only while still in force. A planned outage withdrawn from the plan
        without a revocation is treated as not carried out.
        Failure: the declared window. If PGE removed the failure from the map before
        the declared end, count until the removal; that is the best available estimate
        of when the failure was fixed.
        """
        if self.status is RecordStatus.REVOKED:
            return ()
        if self.status is RecordStatus.ACTIVE:
            return self.windows
        if self.kind is OutageKind.PLANNED:
            return ()
        withdrawn_at = self.withdrawn_at
        if withdrawn_at is None:
            return self.windows
        clipped = []
        for window in self.windows:
            if window.start < withdrawn_at:
                clipped.append(TimeWindow(window.start, min(window.stop, withdrawn_at)))
        return tuple(clipped)

    def duration_within(self, span: TimeWindow) -> timedelta:
        """Sum of counted windows clipped to the given span (e.g. a calendar year)."""
        total = timedelta(0)
        for window in self.counted_windows():
            start = max(window.start, span.start)
            stop = min(window.stop, span.stop)
            if start < stop:
                total += stop - start
        return total


def year_span(year: int, timezone: ZoneInfo) -> TimeWindow:
    if year < 2000 or year > 2100:
        raise InvalidValue(f"Invalid year: {year}")
    return TimeWindow(
        datetime(year, 1, 1, tzinfo=timezone),
        datetime(year + 1, 1, 1, tzinfo=timezone),
    )


@dataclass(frozen=True, slots=True)
class OutageAllowance:
    """Yearly interruption allowances; exceeding one entitles to a rebate. None = not set."""

    planned: timedelta | None = timedelta(hours=35)
    unplanned: timedelta | None = None

    def __post_init__(self) -> None:
        for value in (self.planned, self.unplanned):
            if value is not None and value <= timedelta(0):
                raise InvalidValue("Yearly allowance must be positive")

    def for_kind(self, kind: OutageKind) -> timedelta | None:
        return self.planned if kind is OutageKind.PLANNED else self.unplanned


@dataclass(frozen=True, slots=True)
class SummaryLine:
    outage_id: OutageId
    kind: OutageKind
    description: str
    status: RecordStatus
    windows: tuple[TimeWindow, ...]
    counted: timedelta


@dataclass(frozen=True, slots=True)
class KindTotals:
    kind: OutageKind
    total: timedelta
    counted_outages: int
    allowance: timedelta | None

    @property
    def exceeded(self) -> bool:
        return self.allowance is not None and self.total > self.allowance

    @property
    def remaining(self) -> timedelta | None:
        if self.allowance is None:
            return None
        return max(self.allowance - self.total, timedelta(0))


@dataclass(frozen=True, slots=True)
class AnnualOutageSummary:
    year: int
    city_sym: CitySym
    lines: tuple[SummaryLine, ...]
    totals: tuple[KindTotals, ...]

    def for_kind(self, kind: OutageKind) -> KindTotals:
        for totals in self.totals:
            if totals.kind is kind:
                return totals
        raise DomainError(f"No totals for {kind}")


def summarize_year(
    records: list[OutageRecord],
    year: int,
    city_sym: CitySym,
    timezone: ZoneInfo,
    allowance: OutageAllowance,
) -> AnnualOutageSummary:
    span = year_span(year, timezone)
    lines = tuple(
        SummaryLine(
            outage_id=r.outage_id,
            kind=r.kind,
            description=r.description,
            status=r.status,
            windows=r.windows,
            counted=r.duration_within(span),
        )
        for r in sorted(records, key=lambda r: r.windows[0].start)
        if r.city_sym == city_sym
    )
    totals = tuple(
        KindTotals(
            kind=kind,
            total=sum((line.counted for line in lines if line.kind is kind), timedelta(0)),
            counted_outages=sum(
                1 for line in lines if line.kind is kind and line.counted > timedelta(0)
            ),
            allowance=allowance.for_kind(kind),
        )
        for kind in OutageKind
    )
    return AnnualOutageSummary(year=year, city_sym=city_sym, lines=lines, totals=totals)
