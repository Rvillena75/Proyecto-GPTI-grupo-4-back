"""Shared public capture directory, pacing and process lock for API/CLI workers."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

from scraper.coordination import coordinated_worker
from scraper.store import worker_lock
from scraper.transport import PublicTransport


@contextmanager
def price_transport() -> Iterator[PublicTransport]:
    root = Path(
        os.getenv(
            "STOCKSMART_PRICE_EVIDENCE_DIR", str(Path(gettempdir()) / "stocksmart-public-prices")
        )
    )
    with coordinated_worker() as coordinator, worker_lock(root):
        transport = PublicTransport(root / "evidence" / uuid4().hex, coordinator=coordinator)
        try:
            yield transport
        finally:
            transport.close()
            # Full public HTML is transient. Auditable commercial snapshots live in PostgreSQL.
            import shutil

            target = transport.evidence_dir.resolve()
            if target.parent != (root / "evidence").resolve() or target.is_symlink():
                raise RuntimeError("Unsafe evidence cleanup path")
            shutil.rmtree(target)
