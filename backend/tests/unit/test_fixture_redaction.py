"""Captured prices stay reproducible without publishing the provider's credential strings."""

import hashlib
import json
import re
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_public_fixtures_have_no_aws_ids_or_unredacted_provider_tokens():
    for path in FIXTURES.rglob("*.html"):
        data = path.read_bytes()
        assert re.search(rb"(?:AKIA|ASIA)[A-Z0-9]{16}", data) is None, path.name
        assert re.search(rb"lswToken[^A-Za-z0-9/+]{0,20}[A-Za-z0-9/+]{40}", data) is None, path.name


def test_redacted_artifacts_keep_original_source_hashes_and_valid_public_bytes():
    count = 0
    for path in FIXTURES.rglob("*.html"):
        metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8-sig"))
        if "redaction" not in metadata:
            continue
        count += 1
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["artifact_sha256"]
        assert metadata["artifact_sha256"] != metadata["sha256"]
        assert metadata["redaction"]["original_response_sha256"] == metadata["sha256"]
        observed = json.loads((path.parent / "observed_prices.json").read_text(encoding="utf-8"))
        quote = next(row for row in observed["quotes"] if row["url"] == metadata["url"])
        assert quote["evidence_sha256"] == metadata["sha256"]
    assert count == 3
