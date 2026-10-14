"""File-backed worker handoff until Pedro confirms price/order persistence tables."""

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.e2.dataset import json_text


class WorkerBusyError(RuntimeError):
    """The process lock is held by another price worker."""


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json_text(value), encoding="utf-8", newline="\n")
    temporary.replace(path)


@contextmanager
def worker_lock(directory: Path):
    """One cron/manual worker per store; OS releases the lock even after a crash."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".worker.lock"
    with path.open("a+b") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise WorkerBusyError("Another price worker is already running for this store") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class QuoteStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def add_quote(self, quote: dict) -> None:
        path = self.directory / "quotes" / (quote["id"] + ".json")
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if {k: v for k, v in existing.items() if k != "run_id"} != {
                k: v for k, v in quote.items() if k != "run_id"
            }:
                raise ValueError("Immutable quote ID collision")
            return  # Replays preserve the original quote and its original run.
        save(path, quote)

    def add_run(self, run: dict) -> None:
        save(self.directory / "runs" / (run["id"] + ".json"), run)

    def latest(self, product_id: int) -> dict | None:
        quotes = []
        for path in (self.directory / "quotes").glob("*.json"):
            quote = json.loads(path.read_text(encoding="utf-8"))
            if quote["supplier_product_id"] == product_id:
                quotes.append(quote)
        return max(
            quotes, key=lambda q: (datetime.fromisoformat(q["observed_at"]), q["id"]), default=None
        )

    def last_known_availability(self, product_id: int) -> bool | None:
        observations = []
        for path in (self.directory / "runs").glob("*.json"):
            run = json.loads(path.read_text(encoding="utf-8"))
            for result in run["suppliers"]:
                if result["supplier_product_id"] == product_id and result["status"] in {
                    "OK",
                    "OUT_OF_STOCK",
                }:
                    observations.append(
                        (
                            datetime.fromisoformat(result.get("observed_at", run["finished_at"])),
                            result["status"] == "OK",
                        )
                    )
        return max(observations)[1] if observations else None


def quote_state(quote: dict | None, reference: datetime) -> str:
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("Reference timestamp must be timezone-aware")
    if quote is None:
        return "MISSING"
    if not quote["available"]:
        return "OUT_OF_STOCK"
    observed, valid_until = [
        datetime.fromisoformat(quote[k]) for k in ("observed_at", "valid_until")
    ]
    if reference < observed:
        return "FUTURE_OBSERVATION"
    return "CURRENT" if reference <= valid_until else "EXPIRED"
