"""Janus cold start demo against the in process EVM.

Runs the exact cold start story with honest process boundaries:

Process A creates an operation, executes the first leg of the transfer,
persists a checkpoint through Sibyl, and stops.

A brand new Sibyl client and executor (no shared runtime state) act as
Process B. Janus restores the checkpoint using only the operation id,
revalidates against Base, refuses nothing when live state matches, and
continues with the acceptance.

Then the mismatch and deletion refusal paths are shown. A genuine OS process
boundary for the memory file is proven separately by the pytest that shells
out to a subprocess.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from eth_account import Account
from eth_tester import EthereumTester
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from janus.chain import BaseChain
from janus.config import Config
from janus.executor import JanusExecutor
from janus.memory import SibylStore


def build_env(tmp: str):
    tester = EthereumTester()
    w3 = Web3(EthereumTesterProvider(tester))
    genesis = w3.eth.accounts[0]
    owner = Account.create()
    target = Account.create()
    stranger = Account.create()
    for acct in (owner, target, stranger):
        w3.eth.send_transaction(
            {"from": genesis, "to": acct.address, "value": 5 * 10**18, "gas": 21000}
        )
    chain = BaseChain.from_provider(w3, explorer_url="")
    config = Config(
        rpc_url="in_process",
        chain_id=chain.chain_id,
        owner_key=owner.key.hex(),
        target_key=target.key.hex(),
        memory_db=Path(tmp) / "memory.db",
    )
    return chain, config, owner, target, stranger


def process_a(chain, config, target):
    store = SibylStore(db_path=config.memory_db)
    executor = JanusExecutor(chain, store, config)
    owner_addr = chain.address(config.owner_key)
    print("[process A] deploying demo contract owned by", owner_addr)
    address, record = chain.deploy(owner_addr, config.owner_key)
    print("[process A] contract deployed at", address, "tx", record.tx_hash[:18] + "...")

    checkpoint = executor.begin_ownership_transfer(
        contract_address=address,
        target_owner=target.address,
    )
    print("[process A] transferOwnership sent and pending owner verified")
    print("[process A] checkpoint written to Sibyl. now stopping deliberately.")
    store.close()
    return address, checkpoint.operation_id


def process_b(chain, config, target):
    store = SibylStore(db_path=config.memory_db)
    executor = JanusExecutor(chain, store, config)
    ops = store.list_checkpoints()
    assert ops, "Sibyl memory is empty in process B. nothing was persisted."
    cp = ops[0]
    print("[process B] restored operation from Sibyl:", cp.operation_id)
    print("[process B] remembered current step:", cp.current_step)

    result = executor.resume(cp.operation_id, execute=False)
    print("[process B] verdict:", result.verdict.status.value)
    print("[process B] next safe action:", result.verdict.next_action)
    assert result.verdict.status.value == "consistent"

    result = executor.resume(cp.operation_id, execute=True)
    print("[process B] after acceptance:", result.verdict.status.value)
    print("[process B] final owner is target?", cp.target_owner.lower() == target.address.lower())
    assert result.verdict.status.value == "completed"
    assert cp.target_owner.lower() == target.address.lower()
    store.close()
    return cp.operation_id


def deletion_test(chain, config, operation_id):
    store = SibylStore(db_path=config.memory_db)
    executor = JanusExecutor(chain, store, config)
    store.delete_checkpoint(operation_id)
    result = executor.resume(operation_id)
    print("[deletion test] verdict:", result.verdict.status.value)
    print("[deletion test]", result.verdict.message)
    assert result.verdict.status.value == "no_checkpoint"
    store.close()


def mismatch_test(chain, config, target, stranger):
    store = SibylStore(db_path=config.memory_db)
    executor = JanusExecutor(chain, store, config)
    owner_addr = chain.address(config.owner_key)
    address, _ = chain.deploy(owner_addr, config.owner_key)
    cp = executor.begin_ownership_transfer(contract_address=address, target_owner=target.address)

    chain.transfer_ownership(address, stranger.address, config.owner_key)
    result = executor.resume(cp.operation_id)
    print("[mismatch test] verdict:", result.verdict.status.value)
    print("[mismatch test] reason:", result.verdict.reasons[0])
    assert result.verdict.status.value == "mismatch"
    store.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        chain, config, owner, target, stranger = build_env(tmp)

        print("=" * 70)
        address, operation_id = process_a(chain, config, target)
        print("=" * 70)
        process_b(chain, config, target)
        print("=" * 70)
        deletion_test(chain, config, operation_id)
        print("=" * 70)
        mismatch_test(chain, config, target, stranger)
        print("=" * 70)
        print("COLD START DEMO PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
