from __future__ import annotations

from janus.models import LiveChainState, OperationCheckpoint, VerdictStatus
from janus.reconciler import reconcile


def _cp(**overrides):
    base = {
        "operation_id": "op_1",
        "contract_address": "0x1111111111111111111111111111111111111111",
        "chain_id": 8453,
        "expected_current_owner": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "target_owner": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "current_step": "awaiting_acceptance",
        "completed_steps": ["transfer_submitted", "pending_owner_verified"],
        "next_expected_step": "acceptOwnership",
    }
    base.update(overrides)
    return OperationCheckpoint(**base)


def _live(**overrides):
    base = {
        "contract_address": "0x1111111111111111111111111111111111111111",
        "chain_id": 8453,
        "owner": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "pending_owner": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "block_number": 1234,
    }
    base.update(overrides)
    return LiveChainState(**base)


def test_missing_checkpoint():
    result = reconcile(None, _live())
    assert result.verdict.status == VerdictStatus.NO_CHECKPOINT
    assert "NO EXECUTION CHECKPOINT FOUND" in result.verdict.message


def test_consistent_awaiting_accept():
    result = reconcile(_cp(), _live())
    assert result.verdict.status == VerdictStatus.CONSISTENT
    assert result.verdict.next_action == "acceptOwnership"


def test_owner_mismatch():
    live = _live(owner="0xcccccccccccccccccccccccccccccccccccccccc")
    result = reconcile(_cp(), live)
    assert result.verdict.status == VerdictStatus.MISMATCH
    assert any("Owner mismatch" in r for r in result.verdict.reasons)


def test_pending_mismatch():
    live = _live(pending_owner="0xcccccccccccccccccccccccccccccccccccccccc")
    result = reconcile(_cp(), live)
    assert result.verdict.status == VerdictStatus.MISMATCH
    assert any("Pending owner mismatch" in r for r in result.verdict.reasons)


def test_chain_id_mismatch():
    result = reconcile(_cp(), _live(chain_id=84532))
    assert result.verdict.status == VerdictStatus.MISMATCH
    assert any("Chain id mismatch" in r for r in result.verdict.reasons)


def test_completed_and_confirmed():
    cp = _cp(
        current_step="completed",
        completed_steps=["pending_owner_verified", "owner_verified"],
        expected_current_owner="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        target_owner="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        next_expected_step=None,
    )
    live = _live(
        owner="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        pending_owner="0x" + "0" * 40,
    )
    result = reconcile(cp, live)
    assert result.verdict.status == VerdictStatus.COMPLETED


def test_completed_but_live_reverted():
    cp = _cp(
        current_step="completed",
        completed_steps=["owner_verified"],
        expected_current_owner="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        target_owner="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    live = _live(
        owner="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        pending_owner="0x" + "0" * 40,
    )
    result = reconcile(cp, live)
    assert result.verdict.status == VerdictStatus.MISMATCH


def test_accept_submitted_but_already_landed():
    cp = _cp(
        current_step="accept_submitted",
        completed_steps=["transfer_submitted", "pending_owner_verified", "accept_submitted"],
        expected_current_owner="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        target_owner="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        next_expected_step="verify final owner",
    )
    live = _live(
        owner="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        pending_owner="0x" + "0" * 40,
    )
    result = reconcile(cp, live)
    assert result.verdict.status == VerdictStatus.COMPLETED


def test_accept_submitted_but_did_not_land():
    cp = _cp(
        current_step="accept_submitted",
        completed_steps=["transfer_submitted", "pending_owner_verified", "accept_submitted"],
        next_expected_step="verify final owner",
    )
    live = _live()
    result = reconcile(cp, live)
    assert result.verdict.status == VerdictStatus.CONSISTENT
    assert result.verdict.next_action == "acceptOwnership"


def test_unknown_step_is_mismatch():
    result = reconcile(_cp(current_step="made_up"), _live())
    assert result.verdict.status == VerdictStatus.MISMATCH
