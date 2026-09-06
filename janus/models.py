"""Data models for a Janus operation checkpoint and reconciliation verdicts."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_operation_id() -> str:
    return "op_" + uuid.uuid4().hex[:10]


class OperationType(str, Enum):
    OWNERSHIP_TRANSFER = "ownership_transfer"


class Step(str, Enum):
    INIT_VERIFIED = "initial_state_verified"
    TRANSFER_SUBMITTED = "transfer_submitted"
    PENDING_VERIFIED = "pending_owner_verified"
    CHECKPOINT_WRITTEN = "checkpoint_written"
    AWAITING_ACCEPT = "awaiting_acceptance"
    ACCEPT_SUBMITTED = "accept_submitted"
    OWNER_VERIFIED = "owner_verified"
    COMPLETED = "completed"


class TxRecord(BaseModel):
    step: str
    tx_hash: str
    status: int
    block_number: int


class FailedStep(BaseModel):
    step: str
    error: str
    ts: str


class OperationCheckpoint(BaseModel):
    operation_id: str
    operation_type: OperationType = OperationType.OWNERSHIP_TRANSFER
    contract_address: str
    chain_id: int
    expected_current_owner: str
    target_owner: str
    current_step: str = Step.INIT_VERIFIED.value
    completed_steps: list[str] = Field(default_factory=list)
    attempted_steps: list[str] = Field(default_factory=list)
    transaction_hashes: list[TxRecord] = Field(default_factory=list)
    failed_steps: list[FailedStep] = Field(default_factory=list)
    safety_invariants: dict[str, Any] = Field(default_factory=dict)
    last_verified_block: int | None = None
    next_expected_step: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    timestamps: dict[str, str] = Field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = utc_now()


class LiveChainState(BaseModel):
    contract_address: str
    chain_id: int
    owner: str
    pending_owner: str
    block_number: int
    runtime_code_hash: str | None = None


class VerdictStatus(str, Enum):
    CONSISTENT = "consistent"
    MISMATCH = "mismatch"
    NO_CHECKPOINT = "no_checkpoint"
    COMPLETED = "completed"


class Verdict(BaseModel):
    status: VerdictStatus
    next_action: str | None = None
    reasons: list[str] = Field(default_factory=list)
    message: str = ""


class ResumeResult(BaseModel):
    verdict: Verdict
    checkpoint: OperationCheckpoint | None = None
    live: LiveChainState | None = None
    new_transactions: list[TxRecord] = Field(default_factory=list)
