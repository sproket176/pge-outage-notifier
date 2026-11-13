from __future__ import annotations

from typing import Protocol

from pge_outage_notifier.domain.history import OutageRecord
from pge_outage_notifier.domain.schedule import OutageSchedule
from pge_outage_notifier.domain.values import CitySym, OutageId, TimeWindow


class OutageScheduleRepository(Protocol):
    def get(self, city_sym: CitySym) -> OutageSchedule | None: ...

    def save(self, schedule: OutageSchedule) -> None: ...


class OutageRecordRepository(Protocol):
    def get(self, outage_id: OutageId) -> OutageRecord | None: ...

    def save(self, record: OutageRecord) -> None: ...

    def list_overlapping(self, city_sym: CitySym, span: TimeWindow) -> list[OutageRecord]: ...
