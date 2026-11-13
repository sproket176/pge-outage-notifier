from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import Enum

from pge_outage_notifier.domain.errors import DomainError, ScheduleMismatch
from pge_outage_notifier.domain.events import (
    DomainEvent,
    OutageAnnounced,
    OutageRescheduled,
    OutageRevoked,
    OutageWithdrawn,
    ReminderIssued,
)
from pge_outage_notifier.domain.values import (
    AffectedAddress,
    CitySym,
    OutageDetails,
    OutageId,
    OutageKind,
    OutageSnapshot,
    ReminderDue,
    ReminderKey,
    ReminderKind,
    ReminderPolicy,
    TimeWindow,
)


class OutageStatus(Enum):
    SCHEDULED = "scheduled"
    REVOKED = "revoked"


class Outage:
    """Entity inside the OutageSchedule aggregate. Identity = identifier from the source."""

    def __init__(
        self,
        outage_id: OutageId,
        description: str,
        windows: tuple[TimeWindow, ...],
        addresses: tuple[AffectedAddress, ...],
        kind: OutageKind = OutageKind.PLANNED,
        status: OutageStatus = OutageStatus.SCHEDULED,
        revoked_description: str | None = None,
        reminders_issued: Iterable[ReminderKey] = (),
    ) -> None:
        if not windows:
            raise DomainError(f"Outage {outage_id} must have at least one window")
        self.outage_id = outage_id
        self.description = description
        self.windows = tuple(sorted(windows))
        self.addresses = addresses
        self.kind = kind
        self.status = status
        self.revoked_description = revoked_description
        self._reminders_issued: set[ReminderKey] = set(reminders_issued)

    @property
    def reminders_issued(self) -> frozenset[ReminderKey]:
        return frozenset(self._reminders_issued)

    @classmethod
    def from_details(
        cls, details: OutageDetails, now: datetime, policy: ReminderPolicy | None = None
    ) -> Outage:
        outage = cls(
            outage_id=details.outage_id,
            description=details.description,
            windows=details.windows,
            addresses=details.addresses,
            kind=details.kind,
            status=OutageStatus.REVOKED if details.revoked else OutageStatus.SCHEDULED,
            revoked_description=details.revoked_description,
        )
        if policy is not None:
            outage._suppress_past_reminders(now, policy)
        return outage

    def has_ended(self, now: datetime) -> bool:
        return all(w.has_ended(now) for w in self.windows)

    def apply(
        self, details: OutageDetails, now: datetime, policy: ReminderPolicy | None = None
    ) -> list[DomainEvent]:
        """Reconciles the entity with a fresh source description and returns what changed."""
        events: list[DomainEvent] = []
        self.description = details.description
        self.addresses = details.addresses

        if details.revoked and self.status is OutageStatus.SCHEDULED:
            self.status = OutageStatus.REVOKED
            self.revoked_description = details.revoked_description
            self.windows = details.windows
            events.append(
                OutageRevoked(
                    occurred_at=now,
                    outage_id=self.outage_id,
                    kind=self.kind,
                    description=self.description,
                    addresses=self.addresses,
                    windows=self.windows,
                    reason=self.revoked_description,
                )
            )
            return events

        if not details.revoked and self.status is OutageStatus.REVOKED:
            self.status = OutageStatus.SCHEDULED
            self.revoked_description = None
            self.windows = details.windows
            self._reminders_issued.clear()
            if policy is not None:
                self._suppress_past_reminders(now, policy)
            events.append(
                OutageAnnounced(
                    occurred_at=now,
                    outage_id=self.outage_id,
                    kind=self.kind,
                    description=self.description,
                    addresses=self.addresses,
                    windows=self.windows,
                )
            )
            return events

        if details.windows != self.windows:
            previous = self.windows
            self.windows = details.windows
            kept_starts = {w.start for w in self.windows}
            self._reminders_issued = {
                k for k in self._reminders_issued if k.window_start in kept_starts
            }
            if policy is not None:
                self._suppress_past_reminders(now, policy)
            if self.status is OutageStatus.SCHEDULED:
                events.append(
                    OutageRescheduled(
                        occurred_at=now,
                        outage_id=self.outage_id,
                        kind=self.kind,
                        description=self.description,
                        addresses=self.addresses,
                        previous_windows=previous,
                        windows=self.windows,
                    )
                )
        return events

    def due_reminders(self, now: datetime, policy: ReminderPolicy) -> list[ReminderDue]:
        """Only planned outages get reminders. A failure is already in progress."""
        if self.status is not OutageStatus.SCHEDULED or self.kind is not OutageKind.PLANNED:
            return []
        due: list[ReminderDue] = []
        for window in self.windows:
            if window.has_started(now):
                continue
            for kind in ReminderKind:
                key = ReminderKey(kind, window.start)
                if key in self._reminders_issued:
                    continue
                if policy.reminder_time(kind, window) <= now:
                    due.append(
                        ReminderDue(
                            outage_id=self.outage_id,
                            kind=kind,
                            window=window,
                            description=self.description,
                            addresses=self.addresses,
                        )
                    )
        return due

    def mark_reminded(self, key: ReminderKey) -> None:
        self._reminders_issued.add(key)

    def _suppress_past_reminders(self, now: datetime, policy: ReminderPolicy) -> None:
        """Reminders whose time had already passed when the window became known count as issued.

        The announcement itself (OutageAnnounced / OutageRescheduled) is enough in that
        case; sending a late reminder on top of it would be noise.
        """
        if self.kind is not OutageKind.PLANNED:
            return
        for window in self.windows:
            for kind in ReminderKind:
                if policy.reminder_time(kind, window) <= now:
                    self._reminders_issued.add(ReminderKey(kind, window.start))


class OutageSchedule:
    """Aggregate: the outage schedule for one monitored locality."""

    def __init__(
        self,
        city_sym: CitySym,
        outages: Iterable[Outage] = (),
        last_reconciled_at: datetime | None = None,
    ) -> None:
        self.city_sym = city_sym
        self._outages: dict[OutageId, Outage] = {o.outage_id: o for o in outages}
        self.last_reconciled_at = last_reconciled_at

    @classmethod
    def empty(cls, city_sym: CitySym) -> OutageSchedule:
        return cls(city_sym)

    @property
    def outages(self) -> tuple[Outage, ...]:
        return tuple(self._outages.values())

    def get(self, outage_id: OutageId) -> Outage | None:
        return self._outages.get(outage_id)

    def reconcile(
        self, snapshot: OutageSnapshot, now: datetime, policy: ReminderPolicy | None = None
    ) -> list[DomainEvent]:
        if snapshot.city_sym != self.city_sym:
            raise ScheduleMismatch(
                f"Snapshot is for {snapshot.city_sym}, schedule is for {self.city_sym}"
            )
        events: list[DomainEvent] = []
        seen: set[OutageId] = set()

        for details in snapshot.outages:
            seen.add(details.outage_id)
            existing = self._outages.get(details.outage_id)
            if existing is None:
                outage = Outage.from_details(details, now, policy)
                self._outages[outage.outage_id] = outage
                if outage.status is OutageStatus.SCHEDULED and not outage.has_ended(now):
                    events.append(
                        OutageAnnounced(
                            occurred_at=now,
                            outage_id=outage.outage_id,
                            kind=outage.kind,
                            description=outage.description,
                            addresses=outage.addresses,
                            windows=outage.windows,
                        )
                    )
                continue
            events.extend(existing.apply(details, now, policy))

        for outage_id in list(self._outages):
            if outage_id in seen:
                continue
            outage = self._outages.pop(outage_id)
            if outage.status is OutageStatus.SCHEDULED and not outage.has_ended(now):
                events.append(
                    OutageWithdrawn(
                        occurred_at=now,
                        outage_id=outage.outage_id,
                        kind=outage.kind,
                        description=outage.description,
                        addresses=outage.addresses,
                        windows=outage.windows,
                    )
                )

        self._prune_finished(now)
        self.last_reconciled_at = now
        return events

    def due_reminders(self, now: datetime, policy: ReminderPolicy) -> list[ReminderDue]:
        due: list[ReminderDue] = []
        for outage in self._outages.values():
            due.extend(outage.due_reminders(now, policy))
        due.sort(key=lambda d: (d.window.start, d.kind.value))
        return due

    def mark_reminded(self, due: ReminderDue, now: datetime) -> ReminderIssued:
        outage = self._outages.get(due.outage_id)
        if outage is None:
            raise DomainError(f"Outage {due.outage_id} is not in the schedule")
        outage.mark_reminded(due.key)
        return ReminderIssued(
            occurred_at=now,
            outage_id=outage.outage_id,
            kind=outage.kind,
            description=outage.description,
            addresses=outage.addresses,
            reminder_kind=due.kind,
            window=due.window,
        )

    def _prune_finished(self, now: datetime) -> None:
        for outage_id in list(self._outages):
            if self._outages[outage_id].has_ended(now):
                del self._outages[outage_id]
