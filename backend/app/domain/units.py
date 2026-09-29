"""Only the mass, volume and count conversions required by P2."""

from decimal import Decimal, InvalidOperation

from app.domain.errors import RuleViolation

BASE_UNITS = {"kg", "L", "unidad"}
FACTORS: dict[str, dict[str, Decimal]] = {
    "kg": {"kg": Decimal("1"), "g": Decimal("0.001")},
    "L": {"L": Decimal("1"), "mL": Decimal("0.001")},
    "unidad": {"unidad": Decimal("1")},
}
SCALE = Decimal("0.000001")
MAX_AMOUNT = Decimal("999999999999999999.999999")


def amount(value: Decimal, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
            raise InvalidOperation
        if result > MAX_AMOUNT:
            raise RuleViolation("AMOUNT_OUT_OF_RANGE", "Quantity exceeds NUMERIC(24,6)", 422)
        rounded = result.quantize(SCALE)
        if rounded != result:
            raise RuleViolation("AMOUNT_PRECISION", "Use at most six decimal places", 422)
        return rounded
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise RuleViolation(
            "INVALID_AMOUNT", "Quantity must be finite and nonnegative", 422
        ) from exc


def to_base(value: Decimal, unit: str, base_unit: str, *, allow_zero: bool = False) -> Decimal:
    if base_unit not in BASE_UNITS or unit not in FACTORS[base_unit]:
        raise RuleViolation("INVALID_UNIT", "Unit cannot be converted to item base unit", 422)
    converted = amount(value, allow_zero=allow_zero) * FACTORS[base_unit][unit]
    return amount(converted, allow_zero=allow_zero)
