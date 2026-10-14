"""One price/physical format normalizer for all adapters, orders and receipts."""

import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from urllib.parse import urlsplit

from app.domain.commercial import CommercialPack
from app.domain.errors import RuleViolation
from app.e2.contracts import PackSpec
from scraper.contracts import ALLOWED_HOSTS, RawQuote, ScrapeError


def validate_public_product_url(adapter: str, url: str) -> None:
    parsed = urlsplit(url)
    if (
        adapter not in ALLOWED_HOSTS
        or parsed.scheme != "https"
        or parsed.hostname != ALLOWED_HOSTS[adapter]
    ):
        raise ScrapeError(
            "INVALID_SOURCE", "Only configured official public HTTPS product pages are accepted"
        )
    pattern = r"/p/[^/]+" if adapter == "central" else r"/[^/]+/p"
    if (
        not re.fullmatch(pattern, parsed.path)
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        raise ScrapeError(
            "INVALID_SOURCE", "Internal APIs, query endpoints and credentials are forbidden"
        )


def parse_clp(text: str | None) -> Decimal:
    if text is None or not str(text).strip():
        raise ScrapeError("PRICE_MISSING", "Product has no published normal price")
    value = str(text).strip().replace("CLP", "").replace("$", "").strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
        value = value.replace(".", "")
    elif "," in value:
        value = value.replace(".", "").replace(",", ".")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ScrapeError("PRICE_PARSE_ERROR", "Published price is not a number") from exc
    if not result.is_finite() or result <= 0 or result != result.to_integral_value():
        raise ScrapeError("INVALID_PRICE", "Normal CLP pack price must be positive whole pesos")
    return result


def parse_format(text: str, base_unit: str) -> CommercialPack:
    upper = text.upper()
    if base_unit == "unidad":
        counts = re.findall(r"(?<![\d.])(\d+)\s*(?:UN(?:ID(?:ADES)?)?\b|UNIDADES\b)", upper)
        counts += re.findall(r"PACK\s+DE\s+(\d+)\b", upper)
        if len(counts) != 1:
            raise ScrapeError(
                "UNEXPECTED_FORMAT", "Expected one explicit whole egg count in product name"
            )
        return CommercialPack("bandeja/pack", Decimal(1), "unidad", int(counts[0]), base_unit)
    matches = re.findall(r"(?<![\d.])(\d+(?:[.,]\d+)?)\s*(KG|GR|G|ML|LT|L)\b", upper)
    if len(matches) != 1:
        raise ScrapeError("UNEXPECTED_FORMAT", "Physical mass/volume is missing or ambiguous")
    quantity, unit = matches[0]
    unit = {"KG": "kg", "GR": "g", "G": "g", "ML": "mL", "LT": "L", "L": "L"}[unit]
    packs = re.findall(r"(?:PACK|MANGA|CAJA)\s*[X×]\s*(\d+)\b", upper)
    if len(packs) > 1:
        raise ScrapeError("UNEXPECTED_FORMAT", "Multiple commercial pack counts are ambiguous")
    return CommercialPack(
        "pack", Decimal(quantity.replace(",", ".")), unit, int(packs[0]) if packs else 1, base_unit
    )


def validate_identity(raw: RawQuote, supplier: dict) -> None:
    """Validate the public product even when it is unavailable and has no price."""
    validate_public_product_url(supplier["adapter"], raw.url)
    if raw.currency != "CLP":
        raise ScrapeError("INVALID_CURRENCY", "Expected published CLP prices")
    if (
        raw.sku != supplier["sku"]
        or any(
            token.casefold() not in raw.name_on_site.casefold()
            for token in supplier["expected_name_tokens"]
        )
        or raw.brand.casefold() != supplier["brand"].casefold()
    ):
        raise ScrapeError(
            "PRODUCT_MISMATCH",
            "SKU, brand or product category changed; update configuration explicitly",
        )


def normalize(raw: RawQuote, supplier: dict, observed_at: datetime, evidence_sha256: str) -> dict:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ScrapeError(
            "INVALID_TIMESTAMP", "Original acquisition timestamp must be timezone-aware"
        )
    validate_identity(raw, supplier)
    try:
        expected = PackSpec.model_validate(supplier["pack"]).to_pack()
        published = parse_format(raw.pack_text, expected.base_unit)
        if published.pack_quantity != expected.pack_quantity:
            raise ScrapeError(
                "FORMAT_MISMATCH", "Published format differs from registered commercial product"
            )
        price = parse_clp(raw.price_text)
        normalized = expected.unit_price(price)
    except RuleViolation as exc:
        raise ScrapeError("INVALID_CONVERSION", str(exc)) from exc
    stamp = observed_at.isoformat()
    quote = {
        "id": sha256((raw.url + stamp + evidence_sha256).encode()).hexdigest()[:24],
        "supplier_id": supplier["supplier_id"],
        "supplier": supplier["name"],
        "supplier_product_id": supplier["supplier_product_id"],
        "item_id": supplier["item_id"],
        "name_on_site": raw.name_on_site,
        "sku": raw.sku,
        "brand": raw.brand,
        **expected.as_dict(),
        "pack_price_clp": str(price),
        "unit_price_clp": str(normalized),
        "currency": "CLP",
        "available": raw.available,
        "observed_at": stamp,
        "valid_until": (observed_at + timedelta(days=7)).isoformat(),
        "source_price_valid_until": raw.source_price_valid_until,
        "url": raw.url,
        "acquisition": "PUBLIC_HTTP_GET",
        "evidence_sha256": evidence_sha256,
    }
    if supplier["pack"].get("is_variable_weight"):
        quote.update(is_variable_weight=True, quantity_basis="PUBLISHED_NOMINAL_WEIGHT")
    return quote
