"""Conservative HTTPS GET: system TLS trust, robots, pacing and no retries/redirects."""

import hashlib
import re
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.e2.dataset import write_json
from scraper.contracts import ALLOWED_HOSTS, ScrapeError
from scraper.normalize import validate_public_product_url
from scraper.store import save


@dataclass(frozen=True)
class PublicResponse:
    body: str
    observed_at: datetime
    sha256: str
    url: str


def robots_allowed(body: str, url: str) -> bool:
    """Evaluate the wildcard group; wildcard paths work in the Cencosud robots file."""
    if not re.search(r"(?im)^\s*user-agent\s*:", body):
        raise ScrapeError("ROBOTS_UNREADABLE", "robots.txt is not a readable robots policy")
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []
    for line in body.splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        key = key.lower()
        if key == "user-agent":
            if rules:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.casefold())
        elif key in {"allow", "disallow"}:
            rules.append((key, value))
    groups.append((agents, rules))
    applicable = []
    own = "stocksmart-academic-e2"
    specific = [
        group for group in groups if any(agent != "*" and agent in own for agent in group[0])
    ]
    for _agent_group, rule_group in specific or [g for g in groups if "*" in g[0]]:
        applicable.extend(rule_group)
    target = urlsplit(url).path
    if urlsplit(url).query:
        target += "?" + urlsplit(url).query
    matches = []
    for kind, path in applicable:
        if not path:
            continue
        terminal = path.endswith("$")
        pattern = re.escape(path[:-1] if terminal else path).replace(r"\*", ".*")
        if re.match("^" + pattern + ("$" if terminal else ""), target):
            matches.append((len(path.replace("*", "")), kind == "allow"))
    return max(matches)[1] if matches else True


class PublicTransport:
    def __init__(
        self,
        evidence_dir: Path,
        *,
        client=None,
        interval: float = 2.0,
        monotonic=time.monotonic,
        sleep=time.sleep,
        pacing_path: Path | None = None,
        wall_time=time.time,
    ) -> None:
        if interval < 2:
            raise ValueError("At least two seconds between requests per supplier are required")
        self.evidence_dir = evidence_dir
        evidence_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.Client(
            verify=ssl.create_default_context(),
            follow_redirects=False,
            timeout=25,
            headers={"User-Agent": "StockSmart-Academic-E2/2.0 (public pages; no retries)"},
        )
        self.owns_client = client is None
        self.interval = interval
        self.clock, self.sleep = monotonic, sleep
        self.pacing_path, self.wall_time = pacing_path, wall_time
        self.persisted_times = {}
        if pacing_path and pacing_path.exists():
            import json

            self.persisted_times = json.loads(pacing_path.read_text(encoding="utf-8"))
        self.last: dict[str, float] = {}
        self.blocked: set[str] = set()
        self.responses: dict[str, PublicResponse] = {}

    def close(self) -> None:
        if self.owns_client:
            self.client.close()

    def _get(self, url: str, adapter: str, label: str) -> PublicResponse:
        host = urlsplit(url).hostname
        if host != ALLOWED_HOSTS.get(adapter) or urlsplit(url).scheme != "https":
            raise ScrapeError("INVALID_SOURCE", "Only the configured official host is permitted")
        if host in self.blocked:
            raise ScrapeError("ACCESS_BLOCKED", "Supplier stopped after a block; no bypass/retry")
        delay = 0.0
        if host in self.last:
            delay = self.interval - (self.clock() - self.last[host])
        if host in self.persisted_times:
            delay = max(delay, self.interval - (self.wall_time() - self.persisted_times[host]))
        if delay > 0:
            self.sleep(delay)
        observed = datetime.now(UTC)
        metadata = {
            "url": url,
            "observed_at": observed.isoformat(),
            "acquisition": "PUBLIC_HTTP_GET",
            "redirects_followed": False,
        }
        destination = self.evidence_dir / f"{adapter}_{label}"
        try:
            response = self.client.get(url)
            metadata["status"] = response.status_code
            metadata["content_type"] = response.headers.get("content-type", "")
            body = response.text
            # Hash exactly the UTF-8 text artifact saved for reproducible parser replays.
            encoded = body.encode("utf-8")
            metadata["sha256"] = hashlib.sha256(encoded).hexdigest()
            metadata["bytes"] = len(encoded)
            destination.with_suffix(".txt" if label == "robots" else ".html").write_bytes(encoded)
            write_json(destination.with_suffix(".json"), metadata)
            if response.status_code in {401, 403, 429}:
                self.blocked.add(host)
                raise ScrapeError(
                    "RATE_LIMITED" if response.status_code == 429 else "ACCESS_BLOCKED",
                    f"HTTP {response.status_code}; stop this supplier, no retries",
                )
            if response.status_code == 404:
                raise ScrapeError(
                    "ROBOTS_UNREADABLE" if label == "robots" else "PRODUCT_NOT_FOUND", "HTTP 404"
                )
            if 300 <= response.status_code < 400:
                raise ScrapeError(
                    "REDIRECT_REJECTED",
                    f"HTTP {response.status_code}; inspect public canonical URL",
                )
            if response.status_code != 200:
                raise ScrapeError("HTTP_ERROR", f"HTTP {response.status_code}")
            result = PublicResponse(body, observed, metadata["sha256"], url)
            self.responses[url] = result
            return result
        except httpx.TimeoutException as exc:
            metadata.update(error_code="TIMEOUT", error=str(exc))
            write_json(destination.with_suffix(".json"), metadata)
            raise ScrapeError("TIMEOUT", str(exc)) from exc
        except httpx.HTTPError as exc:
            metadata.update(error_code="NETWORK_ERROR", error=str(exc))
            write_json(destination.with_suffix(".json"), metadata)
            raise ScrapeError("NETWORK_ERROR", str(exc)) from exc
        finally:
            self.last[host] = self.clock()
            if self.pacing_path:
                self.persisted_times[host] = self.wall_time()
                save(self.pacing_path, self.persisted_times)

    def fetch_product(self, url: str, adapter: str) -> PublicResponse:
        validate_public_product_url(adapter, url)
        robots_url = f"https://{ALLOWED_HOSTS[adapter]}/robots.txt"
        policy = self._get(robots_url, adapter, "robots")
        if not robots_allowed(policy.body, url):
            raise ScrapeError("ROBOTS_DENIED", "robots.txt prohibits this product path")
        return self._get(url, adapter, "product")
