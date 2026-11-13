from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pge_outage_notifier.domain.values import (
    AffectedAddress,
    OutageId,
    OutageKind,
    ReminderKind,
    TimeWindow,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    occurred_at: datetime
    outage_id: OutageId
    kind: OutageKind
    description: str
    addresses: tuple[AffectedAddress, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class OutageAnnounced(DomainEvent):
    windows: tuple[TimeWindow, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class OutageRescheduled(DomainEvent):
    previous_windows: tuple[TimeWindow, ...]
    windows: tuple[TimeWindow, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class OutageRevoked(DomainEvent):
    windows: tuple[TimeWindow, ...]
    reason: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class OutageWithdrawn(DomainEvent):
    """The outage disappeared from the source before its end, without an explicit revocation."""

    windows: tuple[TimeWindow, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReminderIssued(DomainEvent):
    reminder_kind: ReminderKind
    window: TimeWindow
