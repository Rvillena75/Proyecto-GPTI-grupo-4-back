"""Private daily PostgreSQL archives, seven daily copies, and guarded isolated restore.

Production mode uploads to a private S3 bucket with server-side AES256 encryption.
No database URL/password is passed as a process argument or written to a manifest.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import database_url

PREFIX = "stocksmart-backup-"


def pg_environment(url: str) -> dict:
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or not parsed.database:
        raise ValueError("PostgreSQL URL required")
    if not parsed.host or not parsed.username:
        raise ValueError("Explicit database host and user required")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(
        PGHOST=parsed.host or "",
        PGPORT=str(parsed.port or 5432),
        PGDATABASE=parsed.database,
        PGUSER=parsed.username or "",
        PGPASSWORD=parsed.password or "",
        PGCONNECT_TIMEOUT="5",
    )
    for key, variable in {
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
    }.items():
        if parsed.query.get(key):
            env[variable] = parsed.query[key]
    return env


def pg_command(name: str) -> str:
    directory = os.getenv("STOCKSMART_PG_BIN")
    candidate = str(Path(directory) / name) if directory else shutil.which(name)
    if not candidate:
        raise RuntimeError("PostgreSQL client tools are required")
    return candidate


def archive(url: str, destination: Path, reference: datetime | None = None) -> dict:
    reference = reference or datetime.now(UTC)
    destination.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        subprocess.run(
            [
                pg_command("pg_dump"),
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(destination / "database.dump"),
            ],
            env=pg_environment(url),
            check=True,
            capture_output=True,
            timeout=300,
        )
        manifest = {
            "format": 1,
            "created_at": reference.isoformat(),
            "alembic_revision": revision,
            "sha256": hashlib.sha256((destination / "database.dump").read_bytes()).hexdigest(),
            "includes": (
                "Schema, data, bcrypt application identities and permissions; "
                "managed database roles recreated by provider"
            ),
        }
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest
    finally:
        engine.dispose()


def daily_local(url: str, directory: Path, reference: datetime | None = None) -> Path:
    """Private persistent operator volume, also used by the restore/retention regression tests."""
    reference = reference or datetime.now(UTC)
    destination = directory / (PREFIX + reference.strftime("%Y-%m-%d"))
    with TemporaryDirectory(prefix="stocksmart-backup-") as temporary:
        staging = Path(temporary)
        archive(url, staging, reference)
        directory.mkdir(parents=True, exist_ok=True)
        destination.mkdir(exist_ok=True)
        if destination.resolve().parent != directory.resolve() or destination.is_symlink():
            raise ValueError("Unsafe backup destination")
        manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
        manifest["dump_filename"] = manifest["sha256"] + ".dump"
        blob = destination / manifest["dump_filename"]
        temporary_blob = destination / (uuid4().hex + ".tmp")
        temporary_manifest = destination / (uuid4().hex + ".tmp")
        try:
            shutil.copyfile(staging / "database.dump", temporary_blob)
            os.replace(temporary_blob, blob)
            temporary_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            # Atomic pointer publication. The old blob remains intact until this succeeds.
            os.replace(temporary_manifest, destination / "manifest.json")
        finally:
            temporary_blob.unlink(missing_ok=True)
            temporary_manifest.unlink(missing_ok=True)
        for old_blob in destination.glob("*.dump"):
            if old_blob != blob:
                old_blob.unlink()
    copies = sorted(
        p
        for p in directory.glob(PREFIX + "????-??-??")
        if p.is_dir() and (p / "manifest.json").is_file()
    )
    for stale in copies[:-7]:
        resolved = stale.resolve()
        if resolved.parent != directory.resolve() or stale.is_symlink():
            raise ValueError("Unsafe retention path")
        shutil.rmtree(resolved)
    return destination


def restore(url: str, source: Path) -> None:
    parsed = make_url(url)
    if not (parsed.database or "").endswith("_test"):
        raise ValueError("Restore requires an explicitly isolated *_test database")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    filename = manifest.get("dump_filename", "database.dump")
    if filename != "database.dump" and not re.fullmatch(r"[0-9a-f]{64}\.dump", filename):
        raise ValueError("Invalid backup filename")
    dump = source / filename
    if dump.resolve().parent != source.resolve():
        raise ValueError("Unsafe backup path")
    if hashlib.sha256(dump.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("Backup hash mismatch")
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")):
                raise ValueError("Restore requires an empty target database")
        subprocess.run(
            [
                pg_command("pg_restore"),
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                parsed.database,
                str(dump),
            ],
            env=pg_environment(url),
            check=True,
            capture_output=True,
            timeout=300,
        )
    finally:
        engine.dispose()


def daily_s3(url: str) -> None:
    import boto3

    bucket = os.environ["STOCKSMART_BACKUP_BUCKET"]
    endpoint = os.getenv("STOCKSMART_BACKUP_ENDPOINT") or None
    if endpoint and urlsplit(endpoint).scheme != "https":
        raise ValueError("Private backup storage requires HTTPS")
    client = boto3.client("s3", endpoint_url=endpoint)
    # Verify public access is blocked; fail closed if the storage cannot certify it.
    block = client.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
    if not all(
        block.get(k)
        for k in (
            "BlockPublicAcls",
            "IgnorePublicAcls",
            "BlockPublicPolicy",
            "RestrictPublicBuckets",
        )
    ):
        raise RuntimeError("Backup bucket must block public access")
    key = PREFIX + datetime.now(UTC).strftime("%Y-%m-%d")
    with TemporaryDirectory(prefix="stocksmart-backup-") as temporary:
        folder = Path(temporary)
        archive(url, folder)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        filename = manifest["sha256"] + ".dump"
        manifest["dump_filename"] = filename
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        for name, target in (("database.dump", filename), ("manifest.json", "manifest.json")):
            client.upload_file(
                str(folder / name),
                bucket,
                key + "/" + target,
                ExtraArgs={"ServerSideEncryption": "AES256"},
            )
    # Only days with published manifest count as complete. List pagination is mandatory.
    owned = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            match = re.fullmatch(
                r"(stocksmart-backup-\d{4}-\d{2}-\d{2})/(manifest\.json|database\.dump|[0-9a-f]{64}\.dump)",
                obj["Key"],
            )
            if match:
                owned.setdefault(match[1], set()).add(obj["Key"])
    if not {key + "/manifest.json", key + "/" + filename} <= owned.get(key, set()):
        raise RuntimeError("Storage did not confirm the complete new backup")
    complete = sorted(
        day
        for day, keys in owned.items()
        if day + "/manifest.json" in keys and any(k.endswith(".dump") for k in keys)
    )
    stale_keys = [entry for day in complete[:-7] for entry in owned[day]]
    stale_keys.extend(
        entry for entry in owned[key] if entry not in {key + "/manifest.json", key + "/" + filename}
    )
    for start in range(0, len(stale_keys), 1000):
        result = client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": entry} for entry in stale_keys[start : start + 1000]]},
        )
        if result.get("Errors"):
            raise RuntimeError("Backup retention deletion failed")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-dir", type=Path)
    parser.add_argument("--restore", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.restore:
            restore(database_url(), args.restore)
        elif args.local_dir:
            daily_local(database_url(), args.local_dir)
        else:
            daily_s3(database_url())
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "type": type(exc).__name__}))
        return 1
    print(json.dumps({"status": "OK"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
