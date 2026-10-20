"""Unattended daily price scraping scheduler for Render Web Service deployments."""

import logging
import os
import threading
from contextlib import suppress
from datetime import UTC, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.persistence.database import session_factory
from scraper.postgres import (
    DEFAULT_TIMEZONE,
    has_unscraped_suppliers,
    is_daily_scrape_due,
    resolve_timezone,
    run_worker,
)

LOG = logging.getLogger("stocksmart.prices.scheduler")
DEFAULT_DAILY_HOUR = 7
DEFAULT_CHECK_SECONDS = 900.0
DEFAULT_STARTUP_DELAY_SECONDS = 5.0


def daily_scrape_enabled() -> bool:
    raw = os.getenv("STOCKSMART_DAILY_SCRAPE_ENABLED")
    if raw is not None:
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return os.getenv("RENDER", "").strip().lower() == "true"


def daily_scrape_timezone() -> str:
    value = os.getenv("STOCKSMART_DAILY_SCRAPE_TIMEZONE", DEFAULT_TIMEZONE).strip()
    resolve_timezone(value)
    return value


def daily_scrape_hour() -> int:
    raw = os.getenv("STOCKSMART_DAILY_SCRAPE_HOUR", str(DEFAULT_DAILY_HOUR)).strip()
    try:
        hour = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            "STOCKSMART_DAILY_SCRAPE_HOUR must be an integer between 0 and 23"
        ) from exc
    if not 0 <= hour <= 23:
        raise RuntimeError("STOCKSMART_DAILY_SCRAPE_HOUR must be between 0 and 23")
    return hour


def next_scheduled_run_at(
    now: datetime,
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
    target_hour: int = DEFAULT_DAILY_HOUR,
) -> datetime:
    zone = resolve_timezone(timezone_name)
    local_now = now.astimezone(zone)
    candidate_date = local_now.date()
    candidate = datetime.combine(candidate_date, time(hour=target_hour), tzinfo=zone)
    if local_now >= candidate:
        candidate = datetime.combine(
            candidate_date + timedelta(days=1), time(hour=target_hour), tzinfo=zone
        )
    return candidate


def should_trigger_daily_scrape(
    session: Session,
    now: datetime,
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
    target_hour: int = DEFAULT_DAILY_HOUR,
) -> tuple[bool, str]:
    zone = resolve_timezone(timezone_name)
    if has_unscraped_suppliers(session):
        return True, "INITIAL_MISSING_RUNS"
    local_now = now.astimezone(zone)
    if local_now.hour < target_hour:
        return False, "BEFORE_TARGET_HOUR"
    if is_daily_scrape_due(session, now, timezone_name=timezone_name):
        return True, "DAILY_SCHEDULE_DUE"
    return False, "ALREADY_COMPLETED_TODAY"


class DailyPriceScheduler:
    """Background daemon that ensures daily scraping runs once per Chile day on Render."""

    def __init__(
        self,
        *,
        timezone_name: str = DEFAULT_TIMEZONE,
        target_hour: int = DEFAULT_DAILY_HOUR,
        check_seconds: float = DEFAULT_CHECK_SECONDS,
        startup_delay_seconds: float = DEFAULT_STARTUP_DELAY_SECONDS,
    ) -> None:
        resolve_timezone(timezone_name)
        self.timezone_name = timezone_name
        self.target_hour = target_hour
        self.check_seconds = max(1.0, float(check_seconds))
        self.startup_delay_seconds = max(0.0, float(startup_delay_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self, now: datetime | None = None) -> tuple[int, dict]:
        current_time = now or datetime.now(UTC)
        try:
            with session_factory()() as session:
                should_run, reason = should_trigger_daily_scrape(
                    session,
                    current_time,
                    timezone_name=self.timezone_name,
                    target_hour=self.target_hour,
                )
                session.rollback()
        except Exception as exc:
            LOG.error("daily_scheduler_check_failed type=%s", type(exc).__name__)
            return 1, {"status": "ERROR", "reason": "CHECK_FAILED", "type": type(exc).__name__}

        next_run = next_scheduled_run_at(
            current_time, timezone_name=self.timezone_name, target_hour=self.target_hour
        )
        if not should_run:
            return 0, {
                "status": "SKIPPED",
                "reason": reason,
                "timezone": self.timezone_name,
                "next_scheduled_at": next_run.isoformat(),
            }

        LOG.info(
            "daily_scheduler_trigger reason=%s timezone=%s next_scheduled_at=%s",
            reason,
            self.timezone_name,
            next_run.isoformat(),
        )
        exit_code, payload = run_worker(
            only_if_due=True,
            timezone_name=self.timezone_name,
            now=current_time,
            emit_stdout=False,
        )
        payload["trigger_reason"] = reason
        payload["next_scheduled_at"] = next_run.isoformat()
        LOG.info(
            "daily_scheduler_finished exit_code=%s status=%s next_scheduled_at=%s",
            exit_code,
            payload.get("status"),
            next_run.isoformat(),
        )
        return exit_code, payload

    def _loop(self) -> None:
        if self.startup_delay_seconds > 0 and self._stop_event.wait(self.startup_delay_seconds):
            return
        while not self._stop_event.is_set():
            with suppress(Exception):
                self.run_once()
            now = datetime.now(UTC)
            next_run = next_scheduled_run_at(
                now, timezone_name=self.timezone_name, target_hour=self.target_hour
            )
            seconds_until_next = max(1.0, (next_run - now).total_seconds())
            wait_seconds = min(self.check_seconds, seconds_until_next)
            if self._stop_event.wait(wait_seconds):
                return

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        next_run = next_scheduled_run_at(
            datetime.now(UTC), timezone_name=self.timezone_name, target_hour=self.target_hour
        )
        LOG.info(
            "daily_scheduler_started timezone=%s target_hour=%02d:00 next_scheduled_at=%s",
            self.timezone_name,
            self.target_hour,
            next_run.isoformat(),
        )
        self._thread = threading.Thread(
            target=self._loop,
            name="stocksmart-daily-price-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
