from dataclasses import dataclass


class ScrapeError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RawQuote:
    name_on_site: str
    price_text: str | None
    pack_text: str
    available: bool
    url: str
    sku: str
    brand: str
    currency: str
    source_price_valid_until: str | None = None


ALLOWED_HOSTS = {
    "central": "www.centralmayorista.cl",
    "jumbo": "www.jumbo.cl",
    "santa_isabel": "www.santaisabel.cl",
}
