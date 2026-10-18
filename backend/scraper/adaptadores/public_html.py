"""Read schema.org/Product from public HTML; no site API and no CSS selectors."""

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from scraper.contracts import RawQuote, ScrapeError
from scraper.normalize import validate_public_product_url


class Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.blocks: list[tuple[dict, str]] = []
        self.current: tuple[dict, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() == "script":
            self.current = (dict(attrs), [])

    def handle_data(self, data: str) -> None:
        if self.current:
            self.current[1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self.current:
            self.blocks.append((self.current[0], "".join(self.current[1])))
            self.current = None


def walk(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def product_objects(body: str) -> list[dict]:
    parser = Scripts()
    parser.feed(body)
    products = []
    parse_error = False
    for attrs, content in parser.blocks:
        if str(attrs.get("type", "")).lower().split(";")[0].strip() != "application/ld+json":
            continue
        try:
            decoded = json.loads(content)
        except ValueError:
            parse_error = True
            continue
        for obj in walk(decoded):
            types = obj.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and "Product" in types:
                products.append(obj)
    if not products:
        raise ScrapeError(
            "PARSING_ERROR" if parse_error else "PRODUCT_NOT_FOUND",
            "No usable schema.org/Product was found in the public HTML",
        )
    return products


def parse_product(body: str, url: str, supplier: dict) -> RawQuote:
    validate_public_product_url(supplier["adapter"], url)
    if re.search(r"<title[^>]*>\s*(?:just a moment|access denied|captcha)|cf-chl-", body, re.I):
        raise ScrapeError("ACCESS_BLOCKED", "Human/anti-bot challenge; no retry or bypass")
    matches = [p for p in product_objects(body) if str(p.get("sku")) == supplier["sku"]]
    if len(matches) != 1:
        raise ScrapeError("PRODUCT_MISMATCH", "Expected exactly one configured product SKU")
    product = matches[0]
    for key in ("url", "@id"):
        if product.get(key) is not None and not isinstance(product[key], str):
            raise ScrapeError("PARSING_ERROR", "Invalid product URL type")
    canonical = product.get("url") or product.get("@id")
    if canonical is not None and not isinstance(canonical, str):
        raise ScrapeError("PARSING_ERROR", "Invalid product URL type")
    if canonical and urljoin(url, canonical).rstrip("/") != url.rstrip("/"):
        raise ScrapeError(
            "PRODUCT_MISMATCH", "Product canonical URL differs from requested public page"
        )
    offers = product.get("offers")
    if isinstance(offers, list):
        if len(offers) != 1:
            raise ScrapeError(
                "PRICE_AMBIGUOUS", "Multiple offers require explicit seller/normal-price validation"
            )
        offers = offers[0]
    if not isinstance(offers, dict) or offers.get("@type") == "AggregateOffer":
        raise ScrapeError("PRICE_MISSING", "Expected a single public regular Offer")
    availability = str(offers.get("availability", "")).rsplit("/", 1)[-1]
    if availability not in {"InStock", "OutOfStock", "SoldOut"}:
        raise ScrapeError("AVAILABILITY_UNKNOWN", "Do not invent stock availability")
    price = offers.get("price")
    specification = offers.get("priceSpecification", {})
    if not isinstance(specification, dict):
        raise ScrapeError("PARSING_ERROR", "Unexpected priceSpecification structure")
    if price is None and isinstance(specification, dict):
        price = specification.get("price")
    brand = product.get("brand", "")
    brand = brand.get("name", "") if isinstance(brand, dict) else brand
    currency = offers.get("priceCurrency") or specification.get("priceCurrency", "")
    if (
        not isinstance(brand, str)
        or not isinstance(currency, str)
        or not isinstance(product.get("name"), str)
        or (
            price is not None
            and (isinstance(price, bool) or not isinstance(price, (str, int, float)))
        )
        or (
            offers.get("priceValidUntil") is not None
            and not isinstance(offers["priceValidUntil"], str)
        )
    ):
        raise ScrapeError("PARSING_ERROR", "Invalid commercial metadata types")
    return RawQuote(
        str(product.get("name", "")),
        str(price) if price is not None else None,
        str(product.get("name", "")),
        availability == "InStock",
        url,
        str(product["sku"]),
        str(brand),
        str(currency),
        offers.get("priceValidUntil"),
    )


class PublicHtmlAdapter:
    def __init__(self, supplier: dict, transport=None) -> None:
        self.supplier = supplier
        self.transport = transport

    def parse(self, body: str, url: str) -> RawQuote:
        return parse_product(body, url, self.supplier)

    def fetch(self, url: str) -> RawQuote:
        if self.transport is None:
            raise ScrapeError("TRANSPORT_REQUIRED", "Use the responsible public HTTP transport")
        result = self.transport.fetch_product(url, self.supplier["adapter"])
        return self.parse(result.body, url)
