from datetime import datetime

import pytest

from pge_outage_notifier.domain.errors import InvalidValue
from pge_outage_notifier.domain.values import CitySym, ReminderKind, TimeWindow
from tests.conftest import at, window


def test_city_sym_must_be_seven_digits() -> None:
    assert str(CitySym("0999001")) == "0999001"
    with pytest.raises(InvalidValue):
        CitySym("279025")
    with pytest.raises(InvalidValue):
        CitySym("abcdefg")


def test_time_window_rejects_naive_and_inverted() -> None:
    with pytest.raises(InvalidValue):
        TimeWindow(datetime(2026, 11, 12, 9), datetime(2026, 11, 12, 17))
    with pytest.raises(InvalidValue):
        window("2026-11-12 17:00", "2026-11-12 09:00")


def test_reminder_policy_times(policy) -> None:
    w = window("2026-11-13 09:00", "2026-11-13 17:00")
    assert policy.reminder_time(ReminderKind.EVENING_BEFORE, w) == at("2026-11-12 18:00")
    assert policy.reminder_time(ReminderKind.SHORTLY_BEFORE, w) == at("2026-11-13 08:00")
