# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
from datetime import UTC, datetime

import pytest

from app.core.money import format_rupees, percent_of, rupees_to_paise
from app.core.time import format_ist, ist_month_bounds, to_ist


def test_ist_display():
    dt = datetime(2026, 10, 6, 18, 30, tzinfo=UTC)
    assert to_ist(dt).day == 7 and to_ist(dt).hour == 0
    assert format_ist(dt) == "07 Oct 2026, 00:00:00 IST"


def test_naive_rejected():
    with pytest.raises(ValueError):
        to_ist(datetime(2026, 1, 1))


def test_ist_month_boundary_is_ist_midnight():
    # 2026-09-30 19:00 UTC is already 1 Oct 00:30 IST
    start, end = ist_month_bounds(datetime(2026, 9, 30, 19, 0, tzinfo=UTC))
    assert start == datetime(2026, 9, 30, 18, 30, tzinfo=UTC)
    assert end == datetime(2026, 10, 31, 18, 30, tzinfo=UTC)


def test_december_rolls_year():
    _, end = ist_month_bounds(datetime(2026, 12, 15, tzinfo=UTC))
    assert end == datetime(2026, 12, 31, 18, 30, tzinfo=UTC)


def test_money():
    assert rupees_to_paise("1234.56") == 123456
    assert rupees_to_paise(10) == 1000
    with pytest.raises(TypeError):
        rupees_to_paise(1.5)  # type: ignore[arg-type]
    assert percent_of(100000, 500) == 5000  # 5%
    assert percent_of(1, 5000) == 1  # 0.5 rounds half-up
    assert format_rupees(12345678) == "₹1,23,456.78"
    assert format_rupees(99) == "₹0.99"
    assert format_rupees(-100000) == "-₹1,000.00"
