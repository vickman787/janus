"""JanusExecutor. Orchestrates the two step ownership transfer lifecycle.

begin_ownership_transfer runs the first leg, persists a checkpoint through
Sibyl, and deliberately stops. resume reopens the operation from memory in a
fresh process, revalidates against Base, and only then continues. Without a
checkpoint, or on a live mismatch, continuation is refused.
"""
from __future__ import annotations

import logging

from janus.chain import BaseChain
from janus.config import Config, ZERO_ADDRESS
from janus.memory import SibylStore
from janus.models import (
    FailedStep,
    LiveChainState,
    OperationCheckpoint,
    ResumeResult,
    Step,
    TxRecord,
    Verdict,
    VerdictStatus,
    new_operation_id,
    utc_now,
)
from janus.reconciler import reconcile

log = logging.getLogger(__name__)


class JanusExecutor:
    def __init__(self, chain: BaseChain, store: SibylStore, config: Config) -> None:
        self.chain = chain
        self.store = store
        self.config = config

    def _owner_address(self) -> str:
        if not self.config.owner_key:
            raise RuntimeError("JANUS_OWNER_KEY is required for the current owner role.")
        return self.chain.address(self.config.owner_key)

    def _target_address(self) -> str:
        if not self.config.target_key:
            raise RuntimeError("JANUS_TARGET_KEY is required for the target owner role.")
        return self.chain.address(self.config.target_key)

    # ------------------------------------------------------------------
    # Begin: first leg of the transfer, then checkpoint and stop
    # ------------------------------------------------------------------
    def begin_ownership_transfer(
        self,
        *,
        contract_address: str | None = None,
        target_owner: str | None = None,
        expected_current_owner: str | None = None,
        operation_id: str | None = None,
    ) -> OperationCheckpoint:
        owner_addr = self._owner_address()
        target = target_owner or self._target_address()
        contract = contract_address or self.config.contract_address
        if not contract:
            raise RuntimeError("No contract address supplied. Deploy or pass JANUS_CONTRACT_ADDRESS.")
        expected = expected_current_owner or owner_addr

        op_id = operation_id or new_operation_id()

        checkpoint = OperationCheckpoint(
            operation_id=op_id,
            contract_address=contract,
            chain_id=self.chain.chain_id,
            expected_current_owner=expected,
            target_owner=target,
            safety_invariants={
                "pending_must_be_zero_on_start": True,
                "live_code_checked": True,
            },
        )
        self.store.set_active(op_id)
        self.store.log_event(op_id, "operation_started", extra={"contract": contract})

        # Read live Base state before any action.
        live_raw = self.chain.live_state(contract)
        live = LiveChainState.model_validate(live_raw)

        reasons = []
        if live.owner.lower() != expected.lower():
            reasons.append(f"Expected current owner {expected} but live owner is {live.owner}.")
        if live.pending_owner.lower() != ZERO_ADDRESS:
            reasons.append(f"Expected zero pending owner but live pending owner is {live.pending_owner}.")
        if reasons:
            checkpoint.failed_steps.append(
                FailedStep(step=Step.INIT_VERIFIED.value, error="; ".join(reasons), ts=utc_now())
            )
            self.store.log_event(
                op_id,
                Step.INIT_VERIFIED.value,
                evaluated={"live": live.model_dump()},
                acted=None,
                extra={"refused": reasons},
            )
            self.store.save_checkpoint(checkpoint)
            raise RuntimeError("; ".join(reasons))

        checkpoint.completed_steps.append(Step.INIT_VERIFIED.value)
        checkpoint.current_step = Step.INIT_VERIFIED.value
        checkpoint.last_verified_block = live.block_number
        checkpoint.next_expected_step = "transferOwnership"
        self.store.log_event(op_id, Step.INIT_VERIFIED.value, evaluated={"live": live.model_dump()})
        self.store.save_checkpoint(checkpoint)

        # Submit the first leg. A restart between this and acceptance is the demo.
        tx = self.chain.transfer_ownership(contract, target, self.config.owner_key)
        checkpoint.attempted_steps.append(Step.TRANSFER_SUBMITTED.value)
        checkpoint.transaction_hashes.append(tx)
        if tx.status != 1:
            checkpoint.failed_steps.append(
                FailedStep(step=Step.TRANSFER_SUBMITTED.value, error=f"transfer tx failed {tx.tx_hash}", ts=utc_now())
            )
            self.store.save_checkpoint(checkpoint)
            raise RuntimeError(f"transferOwnership transaction reverted: {tx.tx_hash}")
        checkpoint.completed_steps.append(Step.TRANSFER_SUBMITTED.value)
        checkpoint.current_step = Step.TRANSFER_SUBMITTED.value
        checkpoint.last_verified_block = tx.block_number
        checkpoint.next_expected_step = "verify pending owner"
        self.store.log_event(
            op_id,
            Step.TRANSFER_SUBMITTED.value,
            acted={"tx_hash": tx.tx_hash, "block": tx.block_number},
        )
        self.store.save_checkpoint(checkpoint)

        # Confirm pendingOwner == target on Base.
        live2_raw = self.chain.live_state(contract)
        live2 = LiveChainState.model_validate(live2_raw)
        if live2.pending_owner.lower() != target.lower():
            checkpoint.failed_steps.append(
                FailedStep(
                    step=Step.PENDING_VERIFIED.value,
                    error=f"pending owner is {live2.pending_owner}, expected {target}",
                    ts=utc_now(),
                )
            )
            self.store.log_event(
                op_id,
                Step.PENDING_VERIFIED.value,
                evaluated={"live": live2.model_dump()},
                acted=None,
                extra={"refused": ["pending owner verification failed"]},
            )
            self.store.save_checkpoint(checkpoint)
            raise RuntimeError("pendingOwner verification failed after transferOwnership")
        checkpoint.completed_steps.append(Step.PENDING_VERIFIED.value)
        checkpoint.current_step = Step.AWAITING_ACCEPT.value
        checkpoint.last_verified_block = live2.block_number
        checkpoint.next_expected_step = "acceptOwnership"
        self.store.log_event(op_id, Step.PENDING_VERIFIED.value, evaluated={"live": live2.model_dump()})
        self.store.save_checkpoint(checkpoint)

        return checkpoint

    # ------------------------------------------------------------------
    # Resume: fresh process restores from Sibyl, revalidates, continues
    # ------------------------------------------------------------------
    def resume(self, operation_id: str, *, execute: bool = True) -> ResumeResult:
        checkpoint = self.store.load_checkpoint(operation_id)
        if checkpoint is None:
            self.store.log_event(
                operation_id,
                "resume_refused",
                extra={"refused": "NO EXECUTION CHECKPOINT FOUND. AUTOMATIC RESUME REFUSED."},
            )
            return ResumeResult(
                verdict=Verdict(
                    status=VerdictStatus.NO_CHECKPOINT,
                    next_action=None,
                    message="NO EXECUTION CHECKPOINT FOUND. AUTOMATIC RESUME REFUSED.",
                    reasons=["Sibyl memory has no record for operation id."],
                )
            )

        live_raw = self.chain.live_state(checkpoint.contract_address)
        live = LiveChainState.model_validate(live_raw)
        result = reconcile(checkpoint, live)

        if result.verdict.status == VerdictStatus.COMPLETED and checkpoint.current_step != "completed":
            # The chain reached the final state but the checkpoint does not know
            # it yet. Record the completion locally and store it in Sibyl.
            if checkpoint.current_step == "accept_submitted":
                checkpoint.completed_steps.append(Step.OWNER_VERIFIED.value)
            checkpoint.completed_steps.append(Step.COMPLETED.value)
            checkpoint.current_step = Step.COMPLETED.value
            checkpoint.last_verified_block = live.block_number
            checkpoint.next_expected_step = None
            checkpoint.safety_invariants["transfer_complete"] = True
            self.store.log_event(
                operation_id,
                Step.COMPLETED.value,
                evaluated={"live": live.model_dump()},
                extra={"note": "completion recorded after reconcile, accept had already landed"},
            )
            self.store.save_checkpoint(checkpoint)
            self.store.clear_active()
            return ResumeResult(
                verdict=Verdict(
                    status=VerdictStatus.COMPLETED,
                    message=(
                        "Acceptance had already landed onchain. Janus verified the final "
                        "owner independently and recorded completion in Sibyl."
                    ),
                ),
                checkpoint=checkpoint,
                live=live,
            )

        if result.verdict.status != VerdictStatus.CONSISTENT:
            if result.verdict.status == VerdictStatus.MISMATCH:
                checkpoint.failed_steps.append(
                    FailedStep(
                        step=checkpoint.current_step,
                        error="; ".join(result.verdict.reasons),
                        ts=utc_now(),
                    )
                )
                self.store.save_checkpoint(checkpoint)
                self.store.log_event(
                    operation_id,
                    "resume_refused",
                    evaluated={"live": live.model_dump()},
                    acted=None,
                    extra={"refused": result.verdict.reasons},
                )
            return result

        # Consistent. Continue only if the caller asked to execute.
        if not execute:
            return result

        return self._continue(checkpoint, live)

    def _continue(self, checkpoint: OperationCheckpoint, live: LiveChainState) -> ResumeResult:
        action = "acceptOwnership"
        if action != "acceptOwnership":
            return ResumeResult(
                verdict=Verdict(
                    status=VerdictStatus.MISMATCH,
                    next_action=None,
                    message=f"No handler for next action {action}.",
                ),
                checkpoint=checkpoint,
                live=live,
            )

        # The acceptance leg must be signed by the target owner.
        if not self.config.target_key:
            return ResumeResult(
                verdict=Verdict(
                    status=VerdictStatus.MISMATCH,
                    message="Target owner key is required to accept ownership.",
                    reasons=["JANUS_TARGET_KEY not configured."],
                ),
                checkpoint=checkpoint,
                live=live,
            )
        signer = self.chain.address(self.config.target_key)
        if signer.lower() != checkpoint.target_owner.lower():
            return ResumeResult(
                verdict=Verdict(
                    status=VerdictStatus.MISMATCH,
                    message="Configured target signer does not match the remembered target owner.",
                    reasons=[f"Signer is {signer}, checkpoint target is {checkpoint.target_owner}."],
                ),
                checkpoint=checkpoint,
                live=live,
            )
        if live.pending_owner.lower() != checkpoint.target_owner.lower():
            return ResumeResult(
                verdict=Verdict(
                    status=VerdictStatus.MISMATCH,
                    message="Live pending owner no longer matches the remembered target.",
                    reasons=[f"Live pending owner is {live.pending_owner}."],
                ),
                checkpoint=checkpoint,
                live=live,
            )

        tx = self.chain.accept_ownership(checkpoint.contract_address, self.config.target_key)
        checkpoint.attempted_steps.append(Step.ACCEPT_SUBMITTED.value)
        checkpoint.transaction_hashes.append(tx)
        if tx.status != 1:
            checkpoint.failed_steps.append(
                FailedStep(step=Step.ACCEPT_SUBMITTED.value, error=f"accept tx failed {tx.tx_hash}", ts=utc_now())
            )
            self.store.save_checkpoint(checkpoint)
            raise RuntimeError(f"acceptOwnership transaction reverted: {tx.tx_hash}")
        checkpoint.completed_steps.append(Step.ACCEPT_SUBMITTED.value)
        checkpoint.current_step = Step.ACCEPT_SUBMITTED.value
        checkpoint.last_verified_block = tx.block_number
        checkpoint.next_expected_step = "verify final owner"
        self.store.log_event(
            checkpoint.operation_id,
            Step.ACCEPT_SUBMITTED.value,
            acted={"tx_hash": tx.tx_hash, "block": tx.block_number},
        )
        self.store.save_checkpoint(checkpoint)

        # Verify the final owner independently on Base.
        live2_raw = self.chain.live_state(checkpoint.contract_address)
        live2 = LiveChainState.model_validate(live2_raw)
        if live2.owner.lower() != checkpoint.target_owner.lower():
            checkpoint.failed_steps.append(
                FailedStep(
                    step=Step.OWNER_VERIFIED.value,
                    error=f"final owner is {live2.owner}, expected {checkpoint.target_owner}",
                    ts=utc_now(),
                )
            )
            self.store.save_checkpoint(checkpoint)
            raise RuntimeError(f"final owner verification failed, owner is {live2.owner}")
        checkpoint.completed_steps.append(Step.OWNER_VERIFIED.value)
        checkpoint.current_step = Step.COMPLETED.value
        checkpoint.last_verified_block = live2.block_number
        checkpoint.next_expected_step = None
        checkpoint.safety_invariants["transfer_complete"] = True
        self.store.log_event(
            checkpoint.operation_id,
            Step.COMPLETED.value,
            evaluated={"live": live2.model_dump()},
            acted={"tx_hash": tx.tx_hash},
        )
        self.store.save_checkpoint(checkpoint)
        self.store.clear_active()

        return ResumeResult(
            verdict=Verdict(
                status=VerdictStatus.COMPLETED,
                message="Ownership transfer completed and verified on Base.",
            ),
            checkpoint=checkpoint,
            live=live2,
            new_transactions=[tx],
        )

    def status(self, operation_id: str, contract_address: str | None = None) -> ResumeResult:
        """Report remembered and live state without acting.

        When Sibyl holds no checkpoint, Janus can still read the chain if the
        caller supplies a contract address. That is the point of the refusal:
        the chain is readable, the execution context is not, so continuation is
        refused anyway.
        """
        checkpoint = self.store.load_checkpoint(operation_id)
        if checkpoint is None:
            live = None
            if contract_address:
                try:
                    live = LiveChainState.model_validate(self.chain.live_state(contract_address))
                except Exception as exc:
                    log.warning("live read failed for %s: %s", contract_address, exc)
            if live is None:
                return ResumeResult(
                    verdict=Verdict(
                        status=VerdictStatus.NO_CHECKPOINT,
                        message="NO EXECUTION CHECKPOINT FOUND. AUTOMATIC RESUME REFUSED.",
                        reasons=["Sibyl memory has no record for operation id."],
                    )
                )
            return reconcile(None, live)
        live_raw = self.chain.live_state(checkpoint.contract_address)
        live = LiveChainState.model_validate(live_raw)
        return reconcile(checkpoint, live)
