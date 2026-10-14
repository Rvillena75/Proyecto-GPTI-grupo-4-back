"""Commercial packs share the inventory's physical unit conversions."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.domain.errors import RuleViolation
from app.domain.units import FACTORS, SCALE, amount, to_base


@dataclass(frozen=True)
class CommercialPack:
    format_name: str
    content_quantity: Decimal
    content_unit: str
    units_per_pack: int
    base_unit: str

    def __post_init__(self) -> None:
        if not self.format_name.strip():
            raise RuleViolation("INVALID_FORMAT", "Commercial format is required", 422)
        if type(self.units_per_pack) is not int or self.units_per_pack <= 0:
            raise RuleViolation("INVALID_FORMAT", "Pack count must be a positive integer", 422)
        amount(self.content_quantity)
        quantity = self.pack_quantity
        if self.base_unit == "unidad" and quantity != quantity.to_integral_value():
            raise RuleViolation("INVALID_FORMAT", "Counted goods require whole units", 422)

    @property
    def conversion_factor(self) -> Decimal:
        # to_base validates compatibility before the factor is accessed.
        to_base(self.content_quantity, self.content_unit, self.base_unit)
        return FACTORS[self.base_unit][self.content_unit]

    @property
    def pack_quantity(self) -> Decimal:
        return to_base(
            self.content_quantity * self.units_per_pack, self.content_unit, self.base_unit
        )

    def quantity_for_packs(self, packs: int) -> Decimal:
        if type(packs) is not int or packs <= 0:
            raise RuleViolation("INVALID_PACKS", "Buy complete positive commercial packs", 422)
        return amount(self.pack_quantity * packs)

    def packs_for(self, required_quantity: Decimal, minimum: int = 1) -> int:
        needed = amount(required_quantity, allow_zero=True)
        if type(minimum) is not int or minimum < 1:
            raise RuleViolation("INVALID_MINIMUM", "Minimum packs must be positive", 422)
        if needed == 0:
            return 0
        return max(minimum, int((needed / self.pack_quantity).to_integral_value(ROUND_CEILING)))

    def unit_price(self, pack_price: Decimal) -> Decimal:
        return (amount(pack_price) / self.pack_quantity).quantize(SCALE, ROUND_HALF_UP)

    def as_dict(self) -> dict:
        return {
            "commercial_format": self.format_name,
            "content_quantity": str(self.content_quantity),
            "content_unit": self.content_unit,
            "units_per_pack": self.units_per_pack,
            "conversion_factor": str(self.conversion_factor),
            "base_unit": self.base_unit,
            "pack_quantity": str(self.pack_quantity),
        }
