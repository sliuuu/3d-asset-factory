"""Settings as the API and the dispatcher see them: environment, then admin overrides.

Admins can override worker URLs and model ids (any `Settings` field ending in `_url`, `_model` or
`_api`, except the database URL) and switch pipelines off, from Admin → Models & services.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import SettingOverride

DISABLED_KEY = "adapters.disabled"
_PREFIX = "env."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def configurable_fields() -> list[str]:
    return [f.name for f in dataclasses.fields(Settings)
            if f.name.endswith(("_url", "_model", "_api")) and f.name != "database_url"]


def overrides(db: Session) -> dict[str, str]:
    allowed = set(configurable_fields())
    out = {}
    for row in db.scalars(select(SettingOverride).where(SettingOverride.key.startswith(_PREFIX))):
        name = row.key[len(_PREFIX):]
        if name in allowed:
            out[name] = row.value.get("v", "")
    return out


def effective_settings(db: Session) -> Settings:
    return dataclasses.replace(get_settings(), **overrides(db))


def disabled_adapters(db: Session) -> set[str]:
    row = db.get(SettingOverride, DISABLED_KEY)
    return set(row.value.get("v", [])) if row else set()


def set_override(db: Session, name: str, value, actor: str) -> None:
    key = name if name == DISABLED_KEY else _PREFIX + name
    row = db.get(SettingOverride, key)
    if value is None:
        if row is not None:
            db.delete(row)
        return
    if row is None:
        row = SettingOverride(key=key)
        db.add(row)
    row.value = {"v": value}
    row.updated_by = actor
    row.updated_at = _now()


def describe_adapters(db: Session) -> list[dict]:
    from .adapters import ADAPTERS
    s = effective_settings(db)
    off = disabled_adapters(db)
    out = []
    for a in ADAPTERS.values():
        d = a.describe(s)
        d["enabled"] = a.name not in off
        if not d["enabled"]:
            d["available"] = False
            d["unavailable_reason"] = "switched off by an admin"
        out.append(d)
    return out
