# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Billing periods for a subscription. Pure date arithmetic, no database.

Periods are anchored on the start date (period k starts at start + k x step months, day clamped
to the month end), so a 31 Jan start gives 28/29 Feb, then 31 Mar, never a drifting 28th. Billing
is in advance: a period's due date is its first day. Dates are calendar dates in IST.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from app.core.time import IST, utc_now

MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}
MAX_PERIODS = 600  # a runaway contract can never fill the table


def ist_today() -> date:
    return utc_now().astimezone(IST).date()


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    due: date


def add_months(anchor: date, months: int) -> date:
    """`anchor` plus whole months; the day clamps to the target month (29 Feb + 12m = 28 Feb)."""
    index = anchor.year * 12 + (anchor.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def contract_end(start: date, contract_term: str) -> date:
    """Last day covered by a contract term that begins on `start`."""
    return add_months(start, MONTHS[contract_term]) - timedelta(days=1)


def periods(
    start: date, billing_frequency: str, contract_term: str, end: date | None = None
) -> list[Period]:
    """One period per billing cycle from `start` to `end` (default: the contract term's end).

    The last period is cut at `end` when the contract ends mid-cycle. A billing frequency longer
    than the contract term is a data error and gives no periods."""
    step = MONTHS[billing_frequency]
    if step > MONTHS[contract_term] and end is None:
        raise ValueError("the billing frequency is longer than the contract term")
    last = end if end is not None else contract_end(start, contract_term)
    out: list[Period] = []
    k = 0
    while True:
        p_start = add_months(start, k * step)
        if p_start > last or len(out) >= MAX_PERIODS:
            break
        p_end = min(add_months(start, (k + 1) * step) - timedelta(days=1), last)
        out.append(Period(p_start, p_end, due=p_start))
        k += 1
    return out
