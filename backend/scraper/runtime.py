"""Shared public capture directory, pacing and process lock for API/CLI workers."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

from scraper.store import worker_lock
from scraper.transport import PublicTransport


@contextmanager
def price_transport() -> Iterator[PublicTransport]:
    root = Path(
        os.getenv(
            "STOCKSMART_PRICE_EVIDENCE_DIR", str(Path(gettempdir()) / "stocksmart-public-prices")
        )
    )
    with worker_lock(root):
        transport = PublicTransport(
            root / "evidence" / uuid4().hex, pacing_path=root / ".request_times.json"
        )
        try:
            yield transport
        finally:
            transport.close()
