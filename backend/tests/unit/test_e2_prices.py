import copy
import json
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest

from scraper.adaptadores.public_html import parse_product
from scraper.contracts import ScrapeError
from scraper.normalize import normalize, parse_clp, parse_format, validate_public_product_url
from scraper.pipeline import load_products, run_pipeline
from scraper.store import QuoteStore, quote_state, worker_lock
from scraper.transport import PublicTransport, robots_allowed

RESOURCES = Path(__file__).resolve().parents[1] / "fixtures/e2_huevos"
CONFIG = RESOURCES / "config/products.json"
EVIDENCE = RESOURCES / "evidencia/2026-10-05"
REFERENCE = datetime.fromisoformat("2026-10-06T04:00:00+00:00")


def supplier() -> dict:
    return load_products(CONFIG)[0]


def html_product(config: dict, **changes) -> str:
    product = {
        "@type": "Product",
        "name": config["name_on_site"],
        "sku": config["sku"],
        "url": config["url"],
        "brand": {"name": config["brand"]},
        "offers": {
            "@type": "Offer",
            "price": "8490",
            "priceCurrency": "CLP",
            "availability": "https://schema.org/InStock",
        },
    }
    product.update(changes)
    return '<html><script type="application/ld+json">' + json.dumps(product) + "</script></html>"


def test_three_observed_public_pages_and_timestamp_preserving_replay(tmp_path) -> None:
    run = run_pipeline(CONFIG, tmp_path, mode="replay", evidence=EVIDENCE, reference=REFERENCE)
    assert run["status"] == "OK"
    assert len(run["quotes"]) == 3
    assert [D(q["unit_price_clp"]) for q in run["quotes"]] == [D(283), D("365.833333"), D("357.5")]
    assert all(q["base_unit"] == "unidad" for q in run["quotes"])
    assert {row["supplier"] for row in run["quotes"]} == {
        "Central Mayorista",
        "Jumbo",
        "Santa Isabel",
    }
    first = {q["id"]: (q["observed_at"], q["run_id"]) for q in run["quotes"]}
    again = run_pipeline(CONFIG, tmp_path, mode="replay", evidence=EVIDENCE, reference=REFERENCE)
    assert {q["id"]: (q["observed_at"], q["run_id"]) for q in again["quotes"]} == first
    assert len(list((tmp_path / "quotes").glob("*.json"))) == 3
    expected = datetime.fromisoformat("2026-10-06T02:33:27.989087+00:00")
    assert datetime.fromisoformat(run["quotes"][0]["observed_at"]) == expected
    # Complete pack costs, not normalized unit price times requested units.
    assert run["comparison"]["recommended"]["packs"] == 4
    assert D(run["comparison"]["recommended"]["total_clp"]) == D(33960)
    assert D(run["comparison"]["next_best_total_clp"]) == D(38610)


def test_relative_canonical_jsonld_graph_and_css_changes_are_supported() -> None:
    config = supplier()
    body = html_product(config, url="/p/huevo-grande-pack-de-30-santa-elvira-5066346")
    body = body.replace("<html>", '<html><div class="new-css-layout">text</div>')
    raw = parse_product(body, config["url"], config)
    assert normalize(raw, config, REFERENCE, "fixture")["pack_quantity"] == "30.000000"
    product = json.loads(body.split('ld+json">')[1].split("</script>")[0])
    graph = '<script type="application/ld+json">' + json.dumps({"@graph": [product]}) + "</script>"
    assert parse_product(graph, config["url"], config).sku == config["sku"]


@pytest.mark.parametrize(
    "change,code",
    [
        ({"sku": "changed"}, "PRODUCT_MISMATCH"),
        ({"url": "https://other.invalid/product"}, "PRODUCT_MISMATCH"),
        ({"offers": None}, "PRICE_MISSING"),
        ({"offers": {"@type": "AggregateOffer", "lowPrice": 1}}, "PRICE_MISSING"),
        ({"offers": [{"price": 1}, {"price": 2}]}, "PRICE_AMBIGUOUS"),
        ({"offers": {"@type": "Offer", "price": 1}}, "AVAILABILITY_UNKNOWN"),
    ],
)
def test_unusable_public_schema_is_reported(change, code) -> None:
    config = supplier()
    with pytest.raises(ScrapeError) as caught:
        parse_product(html_product(config, **change), config["url"], config)
    assert caught.value.code == code


@pytest.mark.parametrize("price", [None, "0", "-1", "NaN", "Infinity", "precio", "1.50"])
def test_missing_or_invalid_price_is_rejected(price) -> None:
    with pytest.raises(ScrapeError):
        parse_clp(price)


def test_price_locale_and_commercial_volume_formats() -> None:
    assert parse_clp("$ 8.490") == D(8490)
    assert parse_clp("CLP 8490") == D(8490)
    assert parse_format("CREMA 200ML PACKx6", "L").pack_quantity == D("1.2")
    assert parse_format("HUEVO 20 UN", "unidad").pack_quantity == 20
    with pytest.raises(ScrapeError):
        parse_format("huevos sin cantidad", "unidad")
    config = supplier()
    raw = parse_product(
        html_product(config, name="HUEVO GRANDE PACK DE 20 SANTA ELVIRA"), config["url"], config
    )
    with pytest.raises(ScrapeError, match="format"):
        normalize(raw, config, REFERENCE, "fixture")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.centralmayorista.cl/api/products",
        "http://www.centralmayorista.cl/p/huevo",
        "https://nextgentheadless.instaleap.io/api/v3",
        "https://www.alvi.cl/product/huevo",
        "https://www.centralmayorista.cl/p/huevo?token=secret",
        "https://user:secret@www.centralmayorista.cl/p/huevo",
    ],
)
def test_internal_endpoints_and_unauthorized_sources_are_forbidden(url) -> None:
    with pytest.raises(ScrapeError):
        validate_public_product_url("central", url)


def test_price_ttl_exactly_seven_days_and_one_microsecond_after() -> None:
    quote = json.loads((EVIDENCE / "observed_prices.json").read_text(encoding="utf-8"))["quotes"][0]
    expiry = datetime.fromisoformat(quote["valid_until"])
    assert quote_state(quote, expiry) == "CURRENT"
    assert quote_state(quote, expiry + timedelta(microseconds=1)) == "EXPIRED"
    assert (
        quote_state(quote, datetime.fromisoformat(quote["observed_at"]) - timedelta(microseconds=1))
        == "FUTURE_OBSERVATION"
    )


class FakeTime:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


@pytest.mark.parametrize(
    "status,code",
    [
        (404, "PRODUCT_NOT_FOUND"),
        (500, "HTTP_ERROR"),
        (403, "ACCESS_BLOCKED"),
        (429, "RATE_LIMITED"),
        (302, "REDIRECT_REJECTED"),
    ],
)
def test_http_failures_are_archived_without_retries_or_redirects(tmp_path, status, code) -> None:
    calls = []
    timer = FakeTime()

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /api/\n")
        return httpx.Response(status, text="error", headers={"Location": "https://other.invalid/"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        transport = PublicTransport(tmp_path, client=client, monotonic=timer.now, sleep=timer.sleep)
        with pytest.raises(ScrapeError) as caught:
            transport.fetch_product(supplier()["url"], "central")
        assert caught.value.code == code
        assert len(calls) == 2
        assert timer.sleeps == [2.0]
        assert list(tmp_path.glob("central_product_*.json"))  # Unique capture per URL/instant.
        if status in {403, 429}:
            with pytest.raises(ScrapeError):
                transport.fetch_product(supplier()["url"], "central")
            assert len(calls) == 2


def test_timeout_robots_denial_and_challenge_are_explicit(tmp_path) -> None:
    calls = []

    def timeout(request):
        calls.append(str(request.url))
        raise httpx.ReadTimeout("fixture timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        transport = PublicTransport(tmp_path, client=client)
        with pytest.raises(ScrapeError) as caught:
            transport.fetch_product(supplier()["url"], "central")
        assert caught.value.code == "TIMEOUT"
        assert len(calls) == 1
    assert not robots_allowed("User-agent: *\nDisallow: /p/*", supplier()["url"])
    assert not robots_allowed("User-agent: *\nDisallow: *?map=", "https://www.jumbo.cl/x?map=a")
    assert robots_allowed("User-agent: *\nDisallow: /p/*\nAllow: /p/huevo*", supplier()["url"])
    with pytest.raises(ScrapeError, match="challenge"):
        parse_product("<title>Just a moment</title>", supplier()["url"], supplier())


def test_failure_keeps_old_current_quote_and_expired_quotes_are_excluded(tmp_path) -> None:
    first = run_pipeline(
        CONFIG, tmp_path / "store", mode="replay", evidence=EVIDENCE, reference=REFERENCE
    )
    damaged = tmp_path / "damaged"
    damaged.mkdir()
    partial = run_pipeline(
        CONFIG, tmp_path / "store", mode="replay", evidence=damaged, reference=REFERENCE
    )
    assert partial["status"] == "ERROR"
    assert len(partial["quotes"]) == 3
    assert all(r["using_previous_current_quote"] for r in partial["suppliers"])
    assert [q["observed_at"] for q in partial["quotes"]] == [
        q["observed_at"] for q in first["quotes"]
    ]
    expired = run_pipeline(
        CONFIG,
        tmp_path / "store",
        mode="replay",
        evidence=damaged,
        reference=REFERENCE + timedelta(days=8),
    )
    assert expired["quotes"] == []
    assert expired["comparison"]["status"] == "INSUFFICIENT_CURRENT_PRICES"
    assert expired["comparison"]["recommended"] is None


def test_out_of_stock_veto_survives_later_failed_refresh(tmp_path) -> None:
    run = run_pipeline(CONFIG, tmp_path, mode="replay", evidence=EVIDENCE, reference=REFERENCE)
    store = QuoteStore(tmp_path)
    old = copy.deepcopy(run)
    old["id"] = "stockout-observation"
    old["suppliers"][0].update(
        status="OUT_OF_STOCK", observed_at=(REFERENCE + timedelta(seconds=1)).isoformat()
    )
    store.add_run(old)
    later = run_pipeline(
        CONFIG,
        tmp_path,
        mode="replay",
        evidence=tmp_path / "missing",
        reference=REFERENCE + timedelta(seconds=2),
    )
    assert len(later["quotes"]) == 2
    assert later["suppliers"][0]["quote_state"] == "OUT_OF_STOCK"


def test_one_corrupted_source_does_not_stop_other_adapters(tmp_path) -> None:
    import shutil

    frozen = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, frozen)
    central = frozen / "central_huevo_grande_30.html"
    central.write_text("corrupted", encoding="utf-8")
    run = run_pipeline(
        CONFIG, tmp_path / "store", mode="replay", evidence=frozen, reference=REFERENCE
    )
    assert run["status"] == "PARTIAL"
    assert run["suppliers"][0]["error_code"] == "EVIDENCE_MISMATCH"
    assert len(run["quotes"]) == 2


def test_worker_concurrency_lock(tmp_path) -> None:
    with worker_lock(tmp_path):
        with pytest.raises(RuntimeError):
            with worker_lock(tmp_path):
                pass


def test_two_second_pacing_survives_a_new_worker(tmp_path) -> None:
    timer = FakeTime()

    def handler(request):
        return httpx.Response(
            200,
            text="User-agent: *\nDisallow:" if request.url.path == "/robots.txt" else "public body",
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for number in range(2):
            transport = PublicTransport(
                tmp_path / str(number),
                client=client,
                monotonic=timer.now,
                sleep=timer.sleep,
                pacing_path=tmp_path / "pacing.json",
                wall_time=timer.now,
            )
            transport.fetch_product(supplier()["url"], "central")
    assert timer.sleeps == [2.0, 2.0, 2.0]
