"""Sibyl Memory persistence layer for Janus checkpoints.

Every operation checkpoint is an entity in the WARM tier under category
"janus.operation" with the operation id as its name. Single source of truth per
operation is enforced by the Sibyl schema UNIQUE (tenant, category, name)
constraint. Lifecycle events are appended to the COLD journal tier. The active
operation pointer lives in the HOT state tier. Full text search runs across the
FTS5 index. No private keys, seeds, or signing material are ever written here.
"""
from __future__ import annotations

import logging
from typing import Any

from sibyl_memory_client import MemoryClient
from sibyl_memory_client.exceptions import NotFoundError

from janus.config import Config, DEFAULT_MEMORY_DB
from janus.models import OperationCheckpoint

log = logging.getLogger(__name__)

OPERATION_CATEGORY = "janus.operation"
ACTIVE_STATE_KEY = "janus.active_operation"
# names that a Sibyl tenant id is checked against are plain identifiers


class SibylStore:
    """A thin typed wrapper around the Sibyl memory client for Janus state."""

    def __init__(self, client: MemoryClient | None = None, db_path: str = DEFAULT_MEMORY_DB) -> None:
        if client is not None:
            self._client = client
        else:
            self._client = MemoryClient.local(db_path)
        self._db_path = db_path

    @property
    def client(self) -> MemoryClient:
        return self._client

    def close(self) -> None:
        """Release the sqlite handle. Windows keeps the file locked otherwise."""
        try:
            self._client.storage.close()
        except Exception:
            pass

    @property
    def db_path(self) -> str:
        return self._db_path

    # ------------------------------------------------------------------
    # Checkpoint write and read (WARM entities, the load bearing tier)
    # ------------------------------------------------------------------
    def save_checkpoint(self, checkpoint: OperationCheckpoint) -> OperationCheckpoint:
        checkpoint.touch()
        body = checkpoint.model_dump()
        self._client.set_entity(OPERATION_CATEGORY, checkpoint.operation_id, body)
        return checkpoint

    def load_checkpoint(self, operation_id: str) -> OperationCheckpoint | None:
        try:
            row = self._client.get_entity(OPERATION_CATEGORY, operation_id)
        except NotFoundError:
            return None
        return OperationCheckpoint.model_validate(row["body"])

    def list_checkpoints(self) -> list[OperationCheckpoint]:
        rows = self._client.list_entities(OPERATION_CATEGORY)
        checkpoints = []
        for row in rows:
            checkpoints.append(OperationCheckpoint.model_validate(row["body"]))
        return checkpoints

    def delete_checkpoint(self, operation_id: str) -> bool:
        return self._client.delete_entity(OPERATION_CATEGORY, operation_id)

    def find_by_target(self, target_owner: str) -> list[OperationCheckpoint]:
        out = []
        for cp in self.list_checkpoints():
            if cp.target_owner.lower() == target_owner.lower():
                out.append(cp)
        return out

    # ------------------------------------------------------------------
    # Active operation pointer (HOT state tier)
    # ------------------------------------------------------------------
    def set_active(self, operation_id: str) -> None:
        self._client.set_state(ACTIVE_STATE_KEY, {"operation_id": operation_id})

    def get_active(self) -> str | None:
        row = self._client.get_state(ACTIVE_STATE_KEY)
        if row is None:
            return None
        body = row["body"]
        return body.get("operation_id") if isinstance(body, dict) else None

    def clear_active(self) -> None:
        self._client.set_state(ACTIVE_STATE_KEY, {"operation_id": None})

    # ------------------------------------------------------------------
    # Journal (COLD tier): append only audit trail
    # ------------------------------------------------------------------
    def log_event(self, operation_id: str, step: str, evaluated: Any = None, acted: Any = None, extra: Any = None) -> str:
        payload = {"operation_id": operation_id, "step": step}
        if extra is not None:
            if isinstance(extra, dict):
                payload.update(extra)
            else:
                payload["detail"] = extra
        return self._client.write_event(evaluated=evaluated, acted=acted, extra=payload)

    def read_events(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._client.read_events(limit=limit)

    def events_for_operation(self, operation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        out = []
        for event in self.read_events(limit=limit):
            extra = event.get("extra") or {}
            if isinstance(extra, dict) and extra.get("operation_id") == operation_id:
                out.append(event)
        return out

    # ------------------------------------------------------------------
    # Search (FTS5)
    # ------------------------------------------------------------------
    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        results = self._client.search_entities(query, limit=limit)
        return [dict(r) for r in results]

    # ------------------------------------------------------------------
    # Export and seed (used for the read only Vercel deployment)
    # ------------------------------------------------------------------
    def export_state(self) -> dict[str, Any]:
        """Serialise checkpoints and journal for a read only snapshot."""
        return {
            "checkpoints": [cp.model_dump() for cp in self.list_checkpoints()],
            "events": self.read_events(limit=1000),
        }

    def import_state(self, state: dict[str, Any]) -> int:
        """Load a snapshot into an empty store. Idempotent by operation id."""
        count = 0
        for raw in state.get("checkpoints", []):
            cp = OperationCheckpoint.model_validate(raw)
            existing = self.load_checkpoint(cp.operation_id)
            if existing is None:
                self.save_checkpoint(cp)
                count += 1
        return count

    def is_empty(self) -> bool:
        return len(self.list_checkpoints()) == 0


def store_from_config(config: Config) -> SibylStore:
    return SibylStore(db_path=config.memory_db)
