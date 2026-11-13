from pge_outage_notifier.domain.events import (
    OutageAnnounced,
    OutageRescheduled,
    OutageRevoked,
    OutageWithdrawn,
)
from pge_outage_notifier.domain.schedule import OutageSchedule, OutageStatus
from pge_outage_notifier.domain.values import OutageId, OutageKind, ReminderKind
from tests.conftest import CITY, at, details, failure, snapshot, window


def test_new_outage_is_announced(policy) -> None:
    schedule = OutageSchedule.empty(CITY)
    events = schedule.reconcile(snapshot(details()), at("2026-11-12 12:00"), policy)
    assert [type(e) for e in events] == [OutageAnnounced]
    assert events[0].windows == (window("2026-11-13 09:00", "2026-11-13 17:00"),)
    assert schedule.get(OutageId("o-1")) is not None


def test_unchanged_outage_is_silent(policy) -> None:
    schedule = OutageSchedule.empty(CITY)
    schedule.reconcile(snapshot(details()), at("2026-11-12 12:00"), policy)
    events = schedule.reconcile(snapshot(details()), at("2026-11-12 12:30"), policy)
    assert events == []


def test_changed_windows_are_rescheduled(policy) -> None:
    schedule = OutageSchedule.empty(CITY)
    schedule.reconcile(snapshot(details()), at("2026-11-12 12:00"), policy)
    moved = details(windows=(window("2026-11-14 08:00", "2026-11-14 15:00"),))
    events = schedule.reconcile(snapshot(moved), at("2026-11-12 13:00"), policy)
    assert [type(e) for e in events] == [OutageRescheduled]
    assert events[0].previous_windows == (window("2026-11-13 09:00", "2026-11-13 17:00"),)
    assert events[0].windows == (window("2026-11-14 08:00", "2026-11-14 15:00"),)


def test_revocation_emits_revoked_once(policy) -> None:
    schedule = OutageSchedule.empty(CITY)
    schedule.reconcile(snapshot(details()), at("2026-11-12 12:00"), policy)
    revoked = details(revoked=True, reason="Prace odwolane")
    events = schedule.reconcile(snapshot(revoked), at("2026-11-12 13:00"), policy)
    assert [type(e) for e in events] == [OutageRevoked]
    assert events[0].reason == "Prace odwolane"
    assert schedule.reconcile(snapshot(revoked), at("2026-11-12 14:00"), policy) == []
    assert schedule.get(OutageId("o-1")).status is OutageStatus.REVOKED


def test_already_revoked_new_outage_is_stored_silently(policy) -> None:
    schedule = OutageSchedule.empty(CITY)
    events = schedule.reconcile(snapshot(details(revoked=True)), at("2026-11-12 12:00"), policy)
    assert events == []
    assert schedule.get(OutageId("o-1")).status is OutageStatus.REVOKED


def test_disappearance_before_end_is_withdrawn(policy) -> None:
    schedule = OutageSchedule.empty(CITY)
    schedule.reconcile(snapshot(details()), at("2026-11-12 12:00"), policy)
    events = schedule.reconcile(snapshot(), at("2026-11-12 13:00"), policy)
    assert [type(e) for e in events] == [OutageWithdrawn]
    assert schedule.outages == ()


def test_finished_outage_is_pruned_without_noise(policy) -> None:
    schedule = OutageSchedule.empty(CITY)
    schedule.reconcile(snapshot(details()), at("2026-11-12 12:00"), policy)
    events = schedule.reconcile(snapshot(), at("2026-11-13 18:00"), policy)
    assert events == []
    assert schedule.outages == ()


def test_outage_announced_in_the_past_is_not_announced(policy) -> None:
    """A first run after the outage has ended must not announce it."""
    schedule = OutageSchedule.empty(CITY)
    events = schedule.reconcile(snapshot(details()), at("2026-11-13 18:00"), policy)
    assert events == []


def test_due_reminders_in_order_and_only_once(policy) -> None:
    schedule = OutageSchedule.empty(CITY)
    schedule.reconcile(snapshot(details()), at("2026-11-12 12:00"), policy)

    assert schedule.due_reminders(at("2026-11-12 17:59"), policy) == []

    due = schedule.due_reminders(at("2026-11-12 18:00"), policy)
    assert [d.kind for d in due] == [ReminderKind.EVENING_BEFORE]
    schedule.mark_reminded(due[0], at("2026-11-12 18:00"))
    assert schedule.due_reminders(at("2026-11-12 18:05"), policy) == []

    due = schedule.due_reminders(at("2026-11-13 08:00"), policy)
    assert [d.kind for d in due] == [ReminderKind.SHORTLY_BEFORE]
    schedule.mark_reminded(due[0], at("2026-11-13 08:00"))
    assert schedule.due_reminders(at("2026-11-13 08:30"), policy) == []


def test_reminders_already_past_at_announcement_are_suppressed(policy) -> None:
    """Outage announced on the day of works: the announcement replaces the evening reminder."""
    schedule = OutageSchedule.empty(CITY)
    schedule.reconcile(snapshot(details()), at("2026-11-13 07:00"), policy)
    assert schedule.due_reminders(at("2026-11-13 07:00"), policy) == []
    due = schedule.due_reminders(at("2026-11-13 08:00"), policy)
    assert [d.kind for d in due] == [ReminderKind.SHORTLY_BEFORE]


def test_no_reminders_once_window_started_or_revoked(policy) -> None:
    schedule = OutageSchedule.empty(CITY)
    schedule.reconcile(snapshot(details()), at("2026-11-12 12:00"), policy)
    assert schedule.due_reminders(at("2026-11-13 09:30"), policy) == []

    schedule.reconcile(snapshot(details(revoked=True)), at("2026-11-12 13:00"), policy)
    assert schedule.due_reminders(at("2026-11-12 18:00"), policy) == []


def test_reschedule_resets_reminders_for_new_window(policy) -> None:
    schedule = OutageSchedule.empty(CITY)
    schedule.reconcile(snapshot(details()), at("2026-11-12 12:00"), policy)
    for due in schedule.due_reminders(at("2026-11-12 18:00"), policy):
        schedule.mark_reminded(due, at("2026-11-12 18:00"))

    moved = details(windows=(window("2026-11-14 09:00", "2026-11-14 17:00"),))
    schedule.reconcile(snapshot(moved), at("2026-11-12 19:00"), policy)
    assert schedule.due_reminders(at("2026-11-12 19:00"), policy) == []
    due = schedule.due_reminders(at("2026-11-13 18:00"), policy)
    assert [d.kind for d in due] == [ReminderKind.EVENING_BEFORE]


def test_multiple_windows_each_get_reminders(policy) -> None:
    two = details(
        windows=(
            window("2026-11-13 09:00", "2026-11-13 12:00"),
            window("2026-11-14 09:00", "2026-11-14 12:00"),
        )
    )
    schedule = OutageSchedule.empty(CITY)
    schedule.reconcile(snapshot(two), at("2026-11-12 12:00"), policy)

    due = schedule.due_reminders(at("2026-11-12 18:00"), policy)
    assert [(d.kind, d.window.start) for d in due] == [
        (ReminderKind.EVENING_BEFORE, at("2026-11-13 09:00")),
    ]

    # The first window is already running, so only the second one gets its evening reminder.
    due = schedule.due_reminders(at("2026-11-13 18:00"), policy)
    assert [(d.kind, d.window.start) for d in due] == [
        (ReminderKind.EVENING_BEFORE, at("2026-11-14 09:00")),
    ]


def test_failure_is_announced_with_kind_and_never_reminded(policy) -> None:
    schedule = OutageSchedule.empty(CITY)
    events = schedule.reconcile(snapshot(failure()), at("2026-11-13 09:00"), policy)
    assert [type(e) for e in events] == [OutageAnnounced]
    assert events[0].kind is OutageKind.UNPLANNED
    assert schedule.due_reminders(at("2026-11-13 09:00"), policy) == []


def test_failure_end_extension_is_rescheduled_and_disappearance_is_withdrawn(policy) -> None:
    schedule = OutageSchedule.empty(CITY)
    schedule.reconcile(snapshot(failure()), at("2026-11-13 09:00"), policy)
    events = schedule.reconcile(
        snapshot(failure(stop="2026-11-13 18:00")), at("2026-11-13 14:00"), policy
    )
    assert [type(e) for e in events] == [OutageRescheduled]
    assert events[0].kind is OutageKind.UNPLANNED
    events = schedule.reconcile(snapshot(), at("2026-11-13 15:00"), policy)
    assert [type(e) for e in events] == [OutageWithdrawn]
