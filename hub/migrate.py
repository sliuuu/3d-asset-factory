"""In-place upgrade for databases created before accounts existed.

`create_all` adds new tables but never new columns, so the columns added to existing tables are
listed here and added with ALTER TABLE (SQLite and Postgres both accept the form used).
"""
from __future__ import annotations

from sqlalchemy import inspect, select, text, update
from sqlalchemy.orm import Session

ADDED_COLUMNS = {
    "assets": {"workspace_id": "VARCHAR(16)", "created_by": "VARCHAR(16)"},
    "runs": {"workspace_id": "VARCHAR(16)", "created_by": "VARCHAR(16)"},
}

DEFAULT_WORKSPACE = "default"


def upgrade(engine) -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in ADDED_COLUMNS.items():
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    from .models import Asset, Run
    with Session(engine) as s:
        ws = default_workspace(s)
        s.execute(update(Asset).where(Asset.workspace_id.is_(None)).values(workspace_id=ws.id))
        for run in s.scalars(select(Run).where(Run.workspace_id.is_(None))):
            asset = s.get(Asset, run.asset_id) if run.asset_id else None
            run.workspace_id = asset.workspace_id if asset else ws.id
        s.commit()


def default_workspace(s: Session):
    from .models import Workspace
    ws = s.scalar(select(Workspace).where(Workspace.slug == DEFAULT_WORKSPACE))
    if ws is None:
        ws = Workspace(slug=DEFAULT_WORKSPACE, name="Default", description="Everything created before workspaces existed")
        s.add(ws)
        s.flush()
    return ws
