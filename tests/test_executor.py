from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from janus.executor import JanusExecutor
from janus.models import VerdictStatus


def _begin(chain_env, deployed):
    executor: JanusExecutor = chain_env["executor"]
    target = chain_env["target"]
    cp = executor.begin_ownership_transfer(contract_address=deployed, target_owner=target.address)
    return cp


def test_full_lifecycle_restores_in_fresh_session(chain_env, deployed, tmp_path):
    chain = chain_env["chain"]
    target = chain_env["target"]
    executor = chain_env["executor"]
    db_path = chain_env["db_path"]

    cp = _begin(chain_env, deployed)
    assert cp.current_step == "awaiting_acceptance"
    assert "pending_owner_verified" in cp.completed_steps
    assert len(cp.transaction_hashes) == 1

    # Simulate a completely fresh process: a brand new Sibyl client on the same
    # db file, a brand new executor object, no shared runtime state.
    fresh_store = __import__("janus.memory", fromlist=["SibylStore"]).SibylStore(db_path=db_path)
    fresh_executor = JanusExecutor(chain, fresh_store, chain_env["config"])

    restored = fresh_store.load_checkpoint(cp.operation_id)
    assert restored is not None
    assert restored.operation_id == cp.operation_id

    result = fresh_executor.resume(cp.operation_id)
    assert result.verdict.status == VerdictStatus.COMPLETED

    final_cp = fresh_store.load_checkpoint(cp.operation_id)
    assert final_cp.current_step == "completed"
    assert "owner_verified" in final_cp.completed_steps
    assert chain.owner(deployed).lower() == target.address.lower()
    assert chain.pending_owner(deployed) == "0x" + "0" * 40

    journal = fresh_store.events_for_operation(cp.operation_id)
    assert len(journal) >= 6


def test_resume_without_execution_reports_consistent(chain_env, deployed):
    executor = chain_env["executor"]
    cp = _begin(chain_env, deployed)
    result = executor.resume(cp.operation_id, execute=False)
    assert result.verdict.status == VerdictStatus.CONSISTENT
    assert result.verdict.next_action == "acceptOwnership"


def test_no_checkpoint_refuses_resume(chain_env, deployed):
    executor = chain_env["executor"]
    result = executor.resume("op_nonexistent")
    assert result.verdict.status == VerdictStatus.NO_CHECKPOINT
    assert "NO EXECUTION CHECKPOINT FOUND" in result.verdict.message


def test_deletion_test_removing_memory_breaks_resume(chain_env, deployed):
    executor = chain_env["executor"]
    store = chain_env["store"]
    cp = _begin(chain_env, deployed)

    assert store.delete_checkpoint(cp.operation_id) is True

    result = executor.resume(cp.operation_id)
    assert result.verdict.status == VerdictStatus.NO_CHECKPOINT
    assert "AUTOMATIC RESUME REFUSED" in result.verdict.message


def test_live_mismatch_refuses_continuation(chain_env, deployed):
    chain = chain_env["chain"]
    stranger = chain_env["stranger"]
    executor = chain_env["executor"]
    cp = _begin(chain_env, deployed)

    # While the process is offline, someone else hijacks the pending slot.
    tx = chain.transfer_ownership(deployed, stranger.address, chain_env["owner"].key.hex())
    assert tx.status == 1

    result = executor.resume(cp.operation_id)
    assert result.verdict.status == VerdictStatus.MISMATCH
    assert "Pending owner mismatch" in " ".join(result.verdict.reasons)

    # The target account must not have sent anything: its nonce is unchanged.
    from web3 import Web3

    nonce = chain.w3.eth.get_transaction_count(chain_env["target"].address)
    assert nonce == 0


def test_completed_checkpoint_reconciles(chain_env, deployed):
    executor = chain_env["executor"]
    cp = _begin(chain_env, deployed)
    result = executor.resume(cp.operation_id)
    assert result.verdict.status == VerdictStatus.COMPLETED
    check = executor.status(cp.operation_id)
    assert check.verdict.status == VerdictStatus.COMPLETED


def test_begin_refuses_when_owner_is_wrong(chain_env, deployed):
    chain = chain_env["chain"]
    stranger = chain_env["stranger"]
    owner = chain_env["owner"]

    # Current owner on chain is `owner`, but executor uses a fresh deployer role
    # expectation pointing at the stranger. We emulate by first transferring
    # ownership to the stranger, so live owner is not the configured owner key.
    chain.transfer_ownership(deployed, stranger.address, owner.key.hex())
    chain.accept_ownership(deployed, stranger.key.hex())

    executor = chain_env["executor"]
    import pytest

    with pytest.raises(RuntimeError, match="Expected current owner"):
        executor.begin_ownership_transfer(contract_address=deployed)


def test_memory_roundtrip(chain_env):
    store = chain_env["store"]
    from janus.models import OperationCheckpoint

    cp = OperationCheckpoint(
        operation_id="op_roundtrip",
        contract_address="0x0000000000000000000000000000000000000001",
        chain_id=chain_env["chain"].chain_id,
        expected_current_owner=chain_env["owner"].address,
        target_owner=chain_env["target"].address,
        current_step="awaiting_acceptance",
    )
    store.save_checkpoint(cp)
    loaded = store.load_checkpoint("op_roundtrip")
    assert loaded is not None
    assert loaded.operation_id == "op_roundtrip"
    listed = store.list_checkpoints()
    assert any(c.operation_id == "op_roundtrip" for c in listed)
    store.set_active("op_roundtrip")
    assert store.get_active() == "op_roundtrip"
    events = store.events_for_operation("op_roundtrip")
    assert events == []


def test_search_finds_operation(chain_env, deployed):
    executor = chain_env["executor"]
    store = chain_env["store"]
    cp = _begin(chain_env, deployed)
    hits = store.search(cp.operation_id)
    assert len(hits) >= 1


def test_fresh_process_persistence_across_subprocess(chain_env, deployed):
    db_path = chain_env["db_path"]
    executor = chain_env["executor"]
    cp = _begin(chain_env, deployed)

    script = (
        "import sys; sys.path.insert(0, r'"
        + str(Path(__file__).resolve().parent.parent)
        + "'); "
        "from janus.memory import SibylStore; "
        f"s = SibylStore(db_path=r'{db_path}'); "
        f"c = s.load_checkpoint('{cp.operation_id}'); "
        "assert c is not None, 'checkpoint lost across process'; "
        "assert c.current_step == 'awaiting_acceptance'; "
        "print('OK', c.operation_id)"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "OK" in out.stdout
