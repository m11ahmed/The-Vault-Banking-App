"""Exact money conversions. The database stores whole paisa, never floats."""
from decimal import Decimal, InvalidOperation

MAX_MINOR = 100_000_000_000
_HUNDRED = Decimal(100)
_PAISA = Decimal("0.01")


def parse_minor(value: Decimal | str | int | float) -> int:
    """Validate rupees and convert to whole paisa without silently rounding."""
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Amount must be a valid decimal number.") from exc
    if not amount.is_finite():
        raise ValueError("Amount must be finite.")
    if amount < 0:
        raise ValueError("Amount cannot be negative.")
    if amount.as_tuple().exponent < -2:
        raise ValueError("Amount must have at most two decimal places.")
    if amount > Decimal(MAX_MINOR) / _HUNDRED:
        raise ValueError("Amount cannot exceed 1,000,000,000.")
    return int(amount * _HUNDRED)


def money_decimal(minor: int) -> Decimal:
    """Expose stored integer paisa as a two-decimal rupee amount."""
    return (Decimal(minor) / _HUNDRED).quantize(_PAISA)
