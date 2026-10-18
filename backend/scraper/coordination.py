"""Cross-process/service exclusion and pacing before HTTP, with PostgreSQL session locks."""

import time
from contextlib import contextmanager

from sqlalchemy import text

from app.persistence.database import session_factory
from scraper.store import WorkerBusyError

WORKER_LOCK = 7_123_741_018_002


class Coordinator:
    def __init__(self, connection):
        self.connection = connection

    def before_request(self, host: str, interval: float) -> None:
        # Clock is PostgreSQL, shared between machines (no reliance on wall-clock sync).
        delay = self.connection.scalar(
            text(
                "SELECT EXTRACT(EPOCH FROM last_finished_at + (:seconds * interval '1 second') "
                "- clock_timestamp()) FROM price_host_clocks WHERE host=:host"
            ),
            {"seconds": interval, "host": host},
        )
        if delay and delay > 0:
            time.sleep(float(delay))
        # Reserve a conservative crash interval. A kill during HTTP cannot bypass pacing.
        self.connection.execute(
            text(
                "INSERT INTO price_host_clocks(host,last_finished_at) "
                "VALUES (:host, clock_timestamp() + interval '30 seconds') "
                "ON CONFLICT(host) DO UPDATE SET last_finished_at=EXCLUDED.last_finished_at"
            ),
            {"host": host},
        )

    def after_request(self, host: str) -> None:
        self.connection.execute(
            text(
                "UPDATE price_host_clocks SET last_finished_at=clock_timestamp() WHERE host=:host"
            ),
            {"host": host},
        )


@contextmanager
def coordinated_worker(factory=None):
    engine = (factory or session_factory()).kw["bind"]
    # Dedicated session-level lock: no item/catalog row is locked while HTTP blocks.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        locked = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK})
        if not locked:
            raise WorkerBusyError("A PostgreSQL price worker is already running")
        try:
            # No other operational worker can be alive while we own this lock.
            connection.execute(
                text(
                    "UPDATE scrape_runs SET status='ERROR', finished_at=clock_timestamp(), "
                    "message=:message WHERE status='RUNNING'"
                ),
                {"message": '{"error_code":"WORKER_INTERRUPTED","products":[]}'},
            )
            yield Coordinator(connection)
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})
