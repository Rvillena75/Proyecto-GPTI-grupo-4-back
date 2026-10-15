from scraper.transport import EXTRA_INTERMEDIATES, public_tls_context


def test_public_context_trusts_central_missing_intermediate() -> None:
    subjects = [
        dict(part[0] for part in cert["subject"]).get("commonName")
        for cert in public_tls_context().get_ca_certs()
    ]
    assert "GlobalSign RSA OV SSL CA 2018" in subjects


def test_intermediate_ships_with_scraper_package() -> None:
    assert EXTRA_INTERMEDIATES.is_file()
    assert "BEGIN CERTIFICATE" in EXTRA_INTERMEDIATES.read_text(encoding="utf-8")
