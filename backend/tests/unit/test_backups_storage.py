"""S3 contract tests use a fake storage API; they do not certify a provisioned bucket."""

import hashlib
import json
from datetime import UTC, datetime

import boto3
import pytest

from app import backups


class PrivateStorage:
    def __init__(self, *, public=False, deletion_error=False):
        self.public, self.deletion_error = public, deletion_error
        self.keys = {
            f"stocksmart-backup-2026-10-{day:02d}/{name}"
            for day in range(1, 10)
            for name in ("manifest.json", "database.dump")
        }
        self.uploads, self.deletes = [], []

    def get_public_access_block(self, **kwargs):
        return {
            "PublicAccessBlockConfiguration": {
                key: not self.public
                for key in (
                    "BlockPublicAcls",
                    "IgnorePublicAcls",
                    "BlockPublicPolicy",
                    "RestrictPublicBuckets",
                )
            }
        }

    def upload_file(self, filename, bucket, key, ExtraArgs):
        assert ExtraArgs == {"ServerSideEncryption": "AES256"}
        assert bucket == "isolated-storage-contract"
        self.uploads.append(key)
        self.keys.add(key)

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, **kwargs):
        keys = sorted(self.keys)
        for start in range(0, len(keys), 3):
            yield {"Contents": [{"Key": key} for key in keys[start : start + 3]]}

    def delete_objects(self, **kwargs):
        if self.deletion_error:
            return {"Errors": [{"Code": "AccessDenied"}]}
        for obj in kwargs["Delete"]["Objects"]:
            self.deletes.append(obj["Key"])
            self.keys.discard(obj["Key"])
        return {}


def configure(monkeypatch, storage):
    monkeypatch.setenv("STOCKSMART_BACKUP_BUCKET", "isolated-storage-contract")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: storage)

    def archive(url, folder):
        (folder / "database.dump").write_bytes(b"synthetic contract test only")
        (folder / "manifest.json").write_text(
            json.dumps(
                {
                    "synthetic": True,
                    "sha256": hashlib.sha256((folder / "database.dump").read_bytes()).hexdigest(),
                }
            )
        )

    monkeypatch.setattr(backups, "archive", archive)


def test_public_bucket_fails_before_upload(monkeypatch):
    storage = PrivateStorage(public=True)
    configure(monkeypatch, storage)
    with pytest.raises(RuntimeError, match="block public"):
        backups.daily_s3("not-used-by-fake-storage")
    assert storage.uploads == []


def test_private_encrypted_uploads_and_paginated_seven_copy_retention(monkeypatch):
    storage = PrivateStorage()
    configure(monkeypatch, storage)
    backups.daily_s3("not-used-by-fake-storage")
    assert len(storage.uploads) == 2 and storage.uploads[-1].endswith("/manifest.json")
    assert len([key for key in storage.keys if key.endswith("/manifest.json")]) == 7
    assert storage.deletes


def test_retention_failure_is_not_reported_as_success(monkeypatch):
    storage = PrivateStorage(deletion_error=True)
    configure(monkeypatch, storage)
    with pytest.raises(RuntimeError, match="retention deletion"):
        backups.daily_s3("not-used-by-fake-storage")


def test_plaintext_backup_endpoint_is_rejected(monkeypatch):
    storage = PrivateStorage()
    configure(monkeypatch, storage)
    monkeypatch.setenv("STOCKSMART_BACKUP_ENDPOINT", "http://insecure.invalid")
    with pytest.raises(ValueError, match="HTTPS"):
        backups.daily_s3("not-used-by-fake-storage")
    assert storage.uploads == []


def test_failed_same_day_backup_keeps_previous_manifest_and_blob(monkeypatch, tmp_path):
    generation = []

    def archive(url, folder, reference=None):
        generation.append(1)
        body = f"synthetic backup generation {len(generation)}".encode()
        (folder / "database.dump").write_bytes(body)
        (folder / "manifest.json").write_text(
            json.dumps({"sha256": hashlib.sha256(body).hexdigest()})
        )

    monkeypatch.setattr(backups, "archive", archive)
    now = datetime(2026, 10, 18, tzinfo=UTC)
    destination = backups.daily_local("unused synthetic source", tmp_path, now)
    previous = (destination / "manifest.json").read_bytes()
    replace = backups.os.replace

    def fail_publish(source, target):
        if target.name == "manifest.json":
            raise OSError("controlled manifest publication failure")
        return replace(source, target)

    monkeypatch.setattr(backups.os, "replace", fail_publish)
    with pytest.raises(OSError):
        backups.daily_local("unused synthetic source", tmp_path, now)
    assert (destination / "manifest.json").read_bytes() == previous
    manifest = json.loads(previous)
    assert (
        hashlib.sha256((destination / manifest["dump_filename"]).read_bytes()).hexdigest()
        == manifest["sha256"]
    )
