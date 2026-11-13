from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pge_outage_notifier.domain.values import (
    AffectedAddress,
    CitySym,
    OutageDetails,
    OutageId,
    OutageKind,
    OutageSnapshot,
    ReminderPolicy,
    TimeWindow,
)

TZ = ZoneInfo("Europe/Warsaw")
CITY = CitySym("0999001")


def at(spec: str) -> datetime:
    """'2026-11-12 09:00' -> aware datetime in Europe/Warsaw."""
    return datetime.strptime(spec, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def window(start: str, stop: str) -> TimeWindow:
    return TimeWindow(at(start), at(stop))


def details(
    outage_id: str = "o-1",
    windows: tuple[TimeWindow, ...] | None = None,
    revoked: bool = False,
    reason: str | None = None,
    description: str = "Borówkowo; Jagodowo",
    kind: OutageKind = OutageKind.PLANNED,
) -> OutageDetails:
    return OutageDetails(
        outage_id=OutageId(outage_id),
        description=description,
        windows=windows or (window("2026-11-13 09:00", "2026-11-13 17:00"),),
        addresses=(AffectedAddress("Borówkowo"), AffectedAddress("Jagodowo")),
        kind=kind,
        revoked=revoked,
        revoked_description=reason,
    )


def snapshot(*outages: OutageDetails, taken_at: datetime | None = None) -> OutageSnapshot:
    return OutageSnapshot(
        city_sym=CITY, taken_at=taken_at or at("2026-11-12 12:00"), outages=tuple(outages)
    )


@pytest.fixture
def policy() -> ReminderPolicy:
    return ReminderPolicy(timezone=TZ, evening_before_hour=18, shortly_before=timedelta(minutes=60))


def failure(
    outage_id: str = "f-1",
    start: str = "2026-11-13 08:36",
    stop: str = "2026-11-13 15:00",
) -> OutageDetails:
    return details(
        outage_id=outage_id,
        windows=(window(start, stop),),
        description="Borówkowo; Borówkowo; Jagodowo",
        kind=OutageKind.UNPLANNED,
    )
