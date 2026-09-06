"""Reconciler. Compares a remembered Sibyl checkpoint against live Base state.

The core invariant: Sibyl tells Janus what happened before. Base tells Janus
what is true now. Janus acts only when the two can be safely reconciled. Memory
alone is never treated as current truth.
"""
from __future__ import annotations

from janus.models import (
    LiveChainState,
    OperationCheckpoint,
    ResumeResult,
    Verdict,
    VerdictStatus,
)


def reconcile(checkpoint: OperationCheckpoint | None, live: LiveChainState) -> ResumeResult:
    if checkpoint is None:
        return ResumeResult(
            verdict=Verdict(
                status=VerdictStatus.NO_CHECKPOINT,
                message="NO EXECUTION CHECKPOINT FOUND. AUTOMATIC RESUME REFUSED.",
                reasons=["No operation checkpoint exists in Sibyl memory for this operation id."],
            ),
            live=live,
        )

    reasons: list[str] = []

    if checkpoint.chain_id != live.chain_id:
        reasons.append(
            f"Chain id mismatch. Memory recorded chain {checkpoint.chain_id}. "
            f"Live chain is {live.chain_id}."
        )

    expected_owner = checkpoint.expected_current_owner
    expected_pending = checkpoint.target_owner
    target = checkpoint.target_owner

    if checkpoint.current_step in ("awaiting_acceptance", "transfer_submitted", "pending_owner_verified"):
        if live.owner.lower() != expected_owner.lower():
            reasons.append(
                f"Owner mismatch. Memory expects owner {expected_owner}. "
                f"Live owner is {live.owner}."
            )
        if live.pending_owner.lower() != expected_pending.lower():
            reasons.append(
                f"Pending owner mismatch. Memory expects pending owner {expected_pending}. "
                f"Live pending owner is {live.pending_owner}."
            )
        if not reasons:
            return ResumeResult(
                verdict=Verdict(
                    status=VerdictStatus.CONSISTENT,
                    next_action="acceptOwnership",
                    message="Live state matches the checkpoint. Next safe action is acceptOwnership.",
                ),
                checkpoint=checkpoint,
                live=live,
            )
        return ResumeResult(
            verdict=Verdict(
                status=VerdictStatus.MISMATCH,
                message="Live state disagrees with memory. Automatic continuation refused.",
                reasons=reasons,
            ),
            checkpoint=checkpoint,
            live=live,
        )

    if checkpoint.current_step == "accept_submitted":
        # The accept transaction was recorded but Janus died before the final
        # owner verification read. Only the chain can say whether it landed.
        if (
            live.owner.lower() == target.lower()
            and live.pending_owner.lower() == "0x" + "0" * 40
        ):
            return ResumeResult(
                verdict=Verdict(
                    status=VerdictStatus.COMPLETED,
                    message=(
                        "Acceptance already landed onchain. Live owner is the target. "
                        "Recording completion after independent verification."
                    ),
                ),
                checkpoint=checkpoint,
                live=live,
            )
        if (
            live.owner.lower() == expected_owner.lower()
            and live.pending_owner.lower() == expected_pending.lower()
        ):
            return ResumeResult(
                verdict=Verdict(
                    status=VerdictStatus.CONSISTENT,
                    next_action="acceptOwnership",
                    message=(
                        "Acceptance did not land onchain. The pending owner is still "
                        "the target, so resubmitting acceptance is safe."
                    ),
                ),
                checkpoint=checkpoint,
                live=live,
            )
        return ResumeResult(
            verdict=Verdict(
                status=VerdictStatus.MISMATCH,
                message="Live state after the recorded accept does not match a safe continuation.",
                reasons=[
                    f"Live owner {live.owner} and pending {live.pending_owner} are neither "
                    f"a completed transfer to {target} nor an unaccepted pending transfer."
                ],
            ),
            checkpoint=checkpoint,
            live=live,
        )

    if checkpoint.current_step in ("completed", "owner_verified"):
        if live.owner.lower() != target.lower():
            reasons.append(
                f"Final owner mismatch. Memory says the transfer completed to {target}. "
                f"Live owner is {live.owner}."
            )
        if live.pending_owner.lower() != "0x" + "0" * 40:
            reasons.append(
                f"Pending owner should be cleared after completion. "
                f"Live pending owner is {live.pending_owner}."
            )
        if not reasons:
            return ResumeResult(
                verdict=Verdict(
                    status=VerdictStatus.COMPLETED,
                    message="Transfer is already complete and live state confirms it.",
                ),
                checkpoint=checkpoint,
                live=live,
            )
        return ResumeResult(
            verdict=Verdict(
                status=VerdictStatus.MISMATCH,
                message="Live state disagrees with a memory that claimed completion.",
                reasons=reasons,
            ),
            checkpoint=checkpoint,
            live=live,
        )

    return ResumeResult(
        verdict=Verdict(
            status=VerdictStatus.MISMATCH,
            message=f"No safe continuation exists from remembered step {checkpoint.current_step}.",
            reasons=[f"Unrecognized current_step {checkpoint.current_step} in checkpoint."],
        ),
        checkpoint=checkpoint,
        live=live,
    )
