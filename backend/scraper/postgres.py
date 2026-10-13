"""Daily/on-demand public price worker using the same PostgreSQL boundary as the API."""

import argparse
import json
from datetime import UTC, datetime

from app.api.procurement_schemas import PriceRefreshResponse
from app.persistence.database import session_factory
from app.services.prices import PriceService
from scraper.runtime import price_transport


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item-id", type=int, required=True)
    args = parser.parse_args()
    if args.item_id <= 0:
        parser.error("--item-id must be positive")
    with session_factory()() as session, price_transport() as transport:
        result = PriceService(session).refresh(args.item_id, datetime.now(UTC), transport)
        output = PriceRefreshResponse.model_validate(result).model_dump(mode="json")
        print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
