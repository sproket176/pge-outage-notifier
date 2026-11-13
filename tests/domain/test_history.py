from datetime import timedelta

from pge_outage_notifier.domain.history import (
    ChangeKind,
    OutageAllowance,
    OutageRecord,
    RecordStatus,
    summarize_year,
    year_span,
)
from pge_outage_notifier.domain.values import OutageKind
from tests.conftest import CITY, TZ, at, details, failure, window


def test_observe_new_and_unchanged() -> None:
    record = OutageRecord.observe_new(CITY, details(), at("2026-11-12 12:00"))
    assert record.status is RecordStatus.ACTIVE
    assert [e.kind for e in record.entries] == [ChangeKind.OBSERVED]
    assert record.observe(details(), at("2026-11-12 12:30")) is False
    assert record.last_seen_at == at("2026-11-12 12:30")
    assert len(record.entries) == 1


def test_reschedule_revoke_reinstate_and_withdraw_are_journaled() -> None:
    record = OutageRecord.observe_new(CITY, details(), at("2026-11-12 12:00"))
    moved = details(windows=(window("2026-11-14 08:00", "2026-11-14 15:00"),))
    assert record.observe(moved, at("2026-11-12 13:00")) is True
    assert record.observe(
        details(windows=moved.windows, revoked=True, reason="odwolane"), at("2026-11-12 14:00")
    )
    assert record.status is RecordStatus.REVOKED
    assert record.observe(moved, at("2026-11-12 15:00")) is True
    assert record.status is RecordStatus.ACTIVE
    assert record.mark_withdrawn(at("2026-11-12 16:00")) is True
    assert record.mark_withdrawn(at("2026-11-12 16:30")) is False
    assert [e.kind for e in record.entries] == [
        ChangeKind.OBSERVED,
        ChangeKind.RESCHEDULED,
        ChangeKind.REVOKED,
        ChangeKind.REINSTATED,
        ChangeKind.WITHDRAWN,
    ]
    assert record.entries[2].note == "odwolane"


def test_duration_is_clipped_to_year_and_ignores_revoked() -> None:
    crossing = details(
        outage_id="o-ny",
        windows=(window("2026-12-31 22:00", "2027-01-01 02:00"),),
    )
    record = OutageRecord.observe_new(CITY, crossing, at("2026-12-01 12:00"))
    assert record.duration_within(year_span(2026, TZ)) == timedelta(hours=2)
    assert record.duration_within(year_span(2027, TZ)) == timedelta(hours=2)

    record.observe(
        details(outage_id="o-ny", windows=crossing.windows, revoked=True), at("2026-12-02 12:00")
    )
    assert record.duration_within(year_span(2026, TZ)) == timedelta(0)


def test_summary_counts_only_active_and_flags_exceeded() -> None:
    now = at("2026-11-12 12:00")
    records = [
        OutageRecord.observe_new(CITY, details(outage_id="a"), now),
        OutageRecord.observe_new(
            CITY,
            details(outage_id="b", windows=(window("2026-10-01 08:00", "2026-10-02 14:00"),)),
            now,
        ),
        OutageRecord.observe_new(CITY, details(outage_id="c", revoked=True), now),
        OutageRecord.observe_new(CITY, failure(), now),
    ]
    summary = summarize_year(records, 2026, CITY, TZ, OutageAllowance(planned=timedelta(hours=35)))
    assert [str(line.outage_id) for line in summary.lines] == ["b", "f-1", "a", "c"]

    planned = summary.for_kind(OutageKind.PLANNED)
    assert planned.total == timedelta(hours=38)
    assert planned.counted_outages == 2
    assert planned.exceeded is True
    assert planned.remaining == timedelta(0)

    unplanned = summary.for_kind(OutageKind.UNPLANNED)
    assert unplanned.total == timedelta(hours=6, minutes=24)
    assert unplanned.allowance is None
    assert unplanned.exceeded is False
    assert unplanned.remaining is None

    smaller = summarize_year(records[:1], 2026, CITY, TZ, OutageAllowance())
    assert smaller.for_kind(OutageKind.PLANNED).remaining == timedelta(hours=27)


def test_withdrawn_failure_counts_until_withdrawal_but_planned_counts_nothing() -> None:
    fail = OutageRecord.observe_new(CITY, failure(), at("2026-11-13 09:00"))
    fail.mark_withdrawn(at("2026-11-13 11:36"))
    assert fail.duration_within(year_span(2026, TZ)) == timedelta(hours=3)

    plan = OutageRecord.observe_new(CITY, details(), at("2026-11-12 12:00"))
    plan.mark_withdrawn(at("2026-11-12 13:00"))
    assert plan.duration_within(year_span(2026, TZ)) == timedelta(0)

    late = OutageRecord.observe_new(CITY, failure(), at("2026-11-13 09:00"))
    late.mark_withdrawn(at("2026-11-13 18:00"))
    assert late.duration_within(year_span(2026, TZ)) == timedelta(hours=6, minutes=24)
