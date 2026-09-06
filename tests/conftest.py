from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from eth_account import Account
from eth_tester import EthereumTester
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from janus.chain import BaseChain
from janus.config import Config
from janus.executor import JanusExecutor
from janus.memory import SibylStore


@pytest.fixture()
def chain_env(tmp_path):
    tester = EthereumTester()
    w3 = Web3(EthereumTesterProvider(tester))
    genesis = w3.eth.accounts[0]
    chain = BaseChain.from_provider(w3)

    owner = Account.create()
    target = Account.create()
    stranger = Account.create()

    for acct in (owner, target, stranger):
        w3.eth.send_transaction(
            {"from": genesis, "to": acct.address, "value": 5 * 10**18, "gas": 21000}
        )

    config = Config(
        rpc_url="local",
        chain_id=chain.chain_id,
        owner_key=owner.key.hex(),
        target_key=target.key.hex(),
        memory_db=str(tmp_path / "memory.db"),
        verify_contract_code=True,
    )
    store = SibylStore(db_path=config.memory_db)
    executor = JanusExecutor(chain, store, config)

    return {
        "chain": chain,
        "owner": owner,
        "target": target,
        "stranger": stranger,
        "config": config,
        "store": store,
        "executor": executor,
        "db_path": config.memory_db,
    }


@pytest.fixture()
def deployed(chain_env):
    chain = chain_env["chain"]
    owner = chain_env["owner"]
    address, record = chain.deploy(owner.address, owner.key.hex())
    return address


@pytest.fixture()
def temp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)
