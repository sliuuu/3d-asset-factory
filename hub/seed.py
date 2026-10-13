"""Load the initial catalog (idempotent by name)."""
import json
from pathlib import Path

from sqlalchemy import select

from .db import init_engine, session
from .models import Asset

CATALOG = Path(__file__).resolve().parent.parent / "catalog" / "initial_catalog.json"


def seed(path: Path = CATALOG) -> int:
    init_engine()
    added = 0
    with session() as db:
        for item in json.loads(path.read_text())["assets"]:
            if db.scalar(select(Asset).where(Asset.name == item["name"])) is None:
                db.add(Asset(**item))
                added += 1
        db.commit()
    return added


if __name__ == "__main__":
    print(f"added {seed()} assets")
