import json
import re

from scraper.adaptadores.public_html import PublicHtmlAdapter, Scripts, walk
from scraper.contracts import RawQuote, ScrapeError


class CentralAdapter(PublicHtmlAdapter):
    def parse(self, body: str, url: str) -> RawQuote:
        raw = super().parse(body, url)
        parser = Scripts()
        parser.feed(body)
        decoder = json.JSONDecoder()
        commercial = []
        # Parse literal JSON already embedded in the public page; never execute JavaScript.
        for _, content in parser.blocks:
            for match in re.finditer(r"self\.__next_f\.push\(", content):
                try:
                    payload, _ = decoder.raw_decode(content, match.end())
                except ValueError:
                    continue
                if (
                    not isinstance(payload, list)
                    or len(payload) < 2
                    or not isinstance(payload[1], str)
                ):
                    continue
                for candidate in re.finditer(r'\{\s*"name"\s*:', payload[1]):
                    try:
                        obj, _ = decoder.raw_decode(payload[1], candidate.start())
                    except ValueError:
                        continue
                    for value in walk(obj):
                        if str(value.get("sku")) == raw.sku and "subQty" in value:
                            commercial.append(value)
        if commercial:
            for value in commercial:
                from app.e2.contracts import PackSpec

                expected = PackSpec.model_validate(self.supplier["pack"]).to_pack()
                if expected.base_unit == "unidad":
                    if value.get("subUnit") != "UN" or str(value.get("subQty")) != str(
                        expected.units_per_pack
                    ):
                        raise ScrapeError(
                            "FORMAT_MISMATCH", "Central commercial metadata disagrees with egg pack"
                        )
                elif expected.base_unit == "kg" and self.supplier["pack"].get("is_variable_weight"):
                    from decimal import Decimal

                    from scraper.normalize import parse_clp

                    if (
                        str(value.get("unit", "")).casefold() != "kg"
                        or Decimal(str(value.get("clickMultiplier", 0))) != expected.pack_quantity
                        or parse_clp(raw.price_text)
                        != Decimal(str(value["price"])) * expected.pack_quantity
                    ):
                        raise ScrapeError(
                            "FORMAT_MISMATCH", "Published kg rate/nominal piece mismatch"
                        )
                if value.get("isAvailable") is not raw.available:
                    raise ScrapeError(
                        "AVAILABILITY_CONFLICT", "JSON-LD and commercial availability disagree"
                    )
        return raw
