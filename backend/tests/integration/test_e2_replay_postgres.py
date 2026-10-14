from decimal import Decimal
from pathlib import Path

import pytest

from app.e2.dataset import generate
from app.e2.replay import replay_inventory


def test_whole_dataset_replays_through_existing_postgres_inventory(clean_db) -> None:
    config = Path(__file__).resolve().parents[1] / "fixtures/e2_huevos/config/dataset_v2.json"
    data = generate(config)
    report = replay_inventory(data, clean_db)
    assert report["valid"] and report["no_e3_simulation"]
    assert report["movements_replayed"] == len(data["movements"])
    assert (
        report["receipt_entries"] == 5
    )  # Opening stock is one INITIAL snapshot, not a duplicate ENTRY.
    assert report["final_stock"] == data["summary"]["final_stock"]
    assert Decimal(report["expiry_merma"]) == Decimal(data["summary"]["waste_quantity"])
    assert Decimal(report["last_28_days_consumption"]) == Decimal(
        data["summary"]["last_28_days_consumption"]
    )
    with pytest.raises(ValueError, match="empty dedicated"):
        replay_inventory(data, clean_db)
