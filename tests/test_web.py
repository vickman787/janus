from __future__ import annotations

import pytest
from eth_account import Account
from eth_tester import EthereumTester
from fastapi.testclient import TestClient
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from janus.chain import BaseChain
from janus.config import Config
from janus.memory import SibylStore
from janus.web import create_app


@pytest.fixture()
def client(tmp_path):
    tester = EthereumTester()
    w3 = Web3(EthereumTesterProvider(tester))
    genesis = w3.eth.accounts[0]
    chain = BaseChain.from_provider(w3, explorer_url="https://basescan.org")

    owner = Account.create()
    target = Account.create()
    for acct in (owner, target):
        w3.eth.send_transaction(
            {"from": genesis, "to": acct.address, "value": 5 * 10**18, "gas": 21000}
        )

    config = Config(
        rpc_url="local",
        chain_id=chain.chain_id,
        owner_key=owner.key.hex(),
        target_key=target.key.hex(),
        memory_db=str(tmp_path / "memory.db"),
    )
    store = SibylStore(db_path=config.memory_db)
    app = create_app(config, chain=chain, store=store)
    return TestClient(app), chain, owner, target, config


def test_index_served(client):
    tc, *_ = client
    res = tc.get("/")
    assert res.status_code == 200
    assert "Dashboard" in res.text
    assert "OPERATIONS IN MEMORY" in res.text
    assert "/static/lockup.svg" in res.text


def test_config_endpoint(client):
    tc, chain, *_ = client
    res = tc.get("/api/config")
    assert res.status_code == 200
    body = res.json()
    assert body["connected"] is True
    assert body["has_owner_key"] is True


def test_deploy_begin_continue_lifecycle(client):
    tc, chain, owner, target, config = client

    dep = tc.post("/api/deploy", json={})
    assert dep.status_code == 200, dep.text
    contract = dep.json()["contract_address"]

    begin = tc.post("/api/operations", json={"contract_address": contract})
    assert begin.status_code == 200, begin.text
    op = begin.json()["operation"]
    op_id = op["operation_id"]
    assert op["current_step"] == "awaiting_acceptance"

    listed = tc.get("/api/operations")
    assert listed.status_code == 200
    assert any(o["operation_id"] == op_id for o in listed.json()["operations"])

    # Status view (a fresh restore) reports consistent before continuing.
    status = tc.get(f"/api/operations/{op_id}")
    assert status.status_code == 200
    assert status.json()["verdict"]["status"] == "consistent"
    assert status.json()["operation"]["operation_id"] == op_id

    cont = tc.post(f"/api/operations/{op_id}/continue", json={"execute": True})
    assert cont.status_code == 200, cont.text
    assert cont.json()["verdict"]["status"] == "completed"

    assert chain.owner(contract).lower() == target.address.lower()

    memory = tc.get(f"/api/operations/{op_id}/memory")
    assert memory.status_code == 200
    m = memory.json()
    assert m["exists"] is True
    assert m["event_count"] >= 6


def test_no_checkpoint_returns_409(client):
    tc, *_ = client
    res = tc.post("/api/operations/op_missing/continue", json={"execute": True})
    assert res.status_code == 409
    assert "NO EXECUTION CHECKPOINT FOUND" in res.json()["detail"]


def test_delete_then_continue_409(client):
    tc, chain, owner, target, config = client
    dep = tc.post("/api/deploy", json={}).json()
    contract = dep["contract_address"]
    begin = tc.post("/api/operations", json={"contract_address": contract}).json()
    op_id = begin["operation"]["operation_id"]

    deleted = tc.delete(f"/api/operations/{op_id}")
    assert deleted.status_code == 200

    res = tc.post(f"/api/operations/{op_id}/continue", json={"execute": True})
    assert res.status_code == 409
    assert "NO EXECUTION CHECKPOINT FOUND" in res.json()["detail"]
