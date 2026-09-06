"""View helpers that shape checkpoints and live state for the UI and CLI."""
from __future__ import annotations

from typing import Any

from janus.chain import BaseChain
from janus.models import LiveChainState, OperationCheckpoint

STEP_LABELS = {
    "initial_state_verified": "Initial state verified",
    "transfer_submitted": "Transfer initiated",
    "pending_owner_verified": "Pending owner observed",
    "awaiting_acceptance": "Acceptance pending",
    "accept_submitted": "Acceptance submitted",
    "owner_verified": "Ownership verified",
    "completed": "Completed",
}

STEP_ORDER = [
    "initial_state_verified",
    "transfer_submitted",
    "pending_owner_verified",
    "awaiting_acceptance",
    "accept_submitted",
    "owner_verified",
    "completed",
]


def checkpoint_view(chain: BaseChain, cp: OperationCheckpoint) -> dict[str, Any]:
    completed = set(cp.completed_steps)
    steps = []
    for step in STEP_ORDER:
        state = "done"
        if step not in completed and cp.current_step == step:
            state = "current"
        elif step not in completed:
            state = "pending"
        steps.append({"key": step, "label": STEP_LABELS[step], "state": state})

    txs = []
    for tx in cp.transaction_hashes:
        txs.append(
            {
                "step": tx.step,
                "tx_hash": tx.tx_hash,
                "status": tx.status,
                "block_number": tx.block_number,
                "url": chain.tx_url(tx.tx_hash),
            }
        )
    return {
        "operation_id": cp.operation_id,
        "operation_type": cp.operation_type.value,
        "contract_address": cp.contract_address,
        "contract_url": chain.address_url(cp.contract_address),
        "chain_id": cp.chain_id,
        "expected_current_owner": cp.expected_current_owner,
        "target_owner": cp.target_owner,
        "current_step": cp.current_step,
        "current_step_label": STEP_LABELS.get(cp.current_step, cp.current_step),
        "completed_steps": cp.completed_steps,
        "steps": steps,
        "transaction_hashes": txs,
        "failed_steps": [f.model_dump() for f in cp.failed_steps],
        "safety_invariants": cp.safety_invariants,
        "last_verified_block": cp.last_verified_block,
        "next_expected_step": cp.next_expected_step,
        "created_at": cp.created_at,
        "updated_at": cp.updated_at,
    }


def live_view(chain: BaseChain, live: LiveChainState) -> dict[str, Any]:
    return {
        "contract_address": live.contract_address,
        "chain_id": live.chain_id,
        "owner": live.owner,
        "owner_url": chain.address_url(live.owner),
        "pending_owner": live.pending_owner,
        "pending_owner_url": chain.address_url(live.pending_owner),
        "block_number": live.block_number,
        "runtime_code_hash": live.runtime_code_hash,
    }
