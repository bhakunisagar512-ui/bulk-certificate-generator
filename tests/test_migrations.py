"""The Alembic migration must produce the same schema as the ORM models (no drift)."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import sqlalchemy as sa

from app.db.base import Base

ROOT = Path(__file__).resolve().parent.parent


def test_migration_matches_models():
    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{tmp}/mig.db"
        env = {**os.environ, "DATABASE_URL": url}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True, capture_output=True
        )
        insp = sa.inspect(sa.create_engine(url))
        for table in Base.metadata.sorted_tables:
            assert insp.has_table(table.name), table.name
            migrated = {c["name"]: c for c in insp.get_columns(table.name)}
            assert set(migrated) == {c.name for c in table.columns}, table.name
            for col in table.columns:
                assert migrated[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"
            model_idx = {i.name for i in table.indexes}
            assert model_idx <= {i["name"] for i in insp.get_indexes(table.name)}, table.name


def test_downgrade_then_upgrade_is_clean():
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp}/mig.db"}
        for args in (["upgrade", "head"], ["downgrade", "base"], ["upgrade", "head"]):
            subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, check=True, capture_output=True)
