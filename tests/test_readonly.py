from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from janus.config import Config
from janus.memory import SibylStore
from janus.web import create_app

SEED = Path(__file__).resolve().parent.parent / "seed" / "operations.json"


@pytest.fixture()
def read_only_app(tmp_path):
    """A read only app seeded from the real committed snapshot."""
    cfg = Config(
        rpc_url="local",
        chain_id=8453,
        memory_db=str(tmp_path / "ro.db"),
        read_only=True,
        seed_file=str(SEED),
    )
    store = SibylStore(db_path=cfg.memory_db)
    # import the seed through the store so no chain is needed
    if store.is_empty() and SEED.exists():
        state = json.loads(SEED.read_text(encoding="utf-8"))
        store.import_state(state)
    app = create_app(cfg, store=store)
    return TestClient(app), cfg


def test_read_only_serves_seeded_operations(read_only_app):
    tc, cfg = read_only_app
    res = tc.get("/")
    assert res.status_code == 200
    assert "OPERATIONS IN MEMORY" in res.text


def test_read_only_blocks_write_api(read_only_app):
    tc, cfg = read_only_app
    res = tc.post("/api/operations", json={})
    assert res.status_code == 403
    assert "read only" in res.json()["detail"].lower()


def test_read_only_blocks_continue_page(read_only_app):
    tc, cfg = read_only_app
    res = tc.post("/api/operations/op_demo_mismatch/continue", json={"execute": True})
    assert res.status_code == 403
