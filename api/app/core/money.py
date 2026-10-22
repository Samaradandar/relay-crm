# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Paise integers, rupee formatting, percentage maths. Never floats."""

from decimal import ROUND_HALF_UP, Decimal


def rupees_to_paise(rupees: str | int | Decimal) -> int:
    if isinstance(rupees, float):
        raise TypeError("floats are not allowed for money")
    return int((Decimal(rupees) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def paise_to_rupees(paise: int) -> Decimal:
    return Decimal(paise) / 100


def percent_of(paise: int, basis_points: int) -> int:
    """paise * basis_points / 10000, rounded half-up. 1% = 100 bp."""
    value = Decimal(paise * basis_points) / 10000
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_rupees(paise: int) -> str:
    """Indian digit grouping, e.g. 12345678 paise -> '₹1,23,456.78'."""
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), 100)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join([*groups, tail])
    return f"{sign}₹{s}.{frac:02d}"
