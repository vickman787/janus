"""Configuration for Janus. Reads environment variables with dotenv support."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_MAINNET_CHAIN_ID = 8453
BASE_MAINNET_RPC_URL = "https://mainnet.base.org"
BASE_MAINNET_EXPLORER = "https://basescan.org"
ZERO_ADDRESS = "0x" + "0" * 40
DEFAULT_MEMORY_DB = str(Path.home() / ".sibyl-memory" / "janus.db")
DEFAULT_SOLC = "0.8.28"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_SOURCE = PROJECT_ROOT / "contracts" / "Ownable2Step.sol"
ARTIFACT_PATH = Path(__file__).resolve().parent / "artifacts" / "Ownable2Step.json"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_optional(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


@dataclass
class Config:
    rpc_url: str = BASE_MAINNET_RPC_URL
    chain_id: int = BASE_MAINNET_CHAIN_ID
    explorer_url: str = BASE_MAINNET_EXPLORER
    owner_key: str | None = None
    target_key: str | None = None
    contract_address: str | None = None
    memory_db: str = DEFAULT_MEMORY_DB
    solc_version: str = DEFAULT_SOLC
    verify_contract_code: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            rpc_url=_env("JANUS_RPC_URL", BASE_MAINNET_RPC_URL),
            chain_id=int(_env("JANUS_CHAIN_ID", str(BASE_MAINNET_CHAIN_ID))),
            explorer_url=_env("JANUS_EXPLORER_URL", BASE_MAINNET_EXPLORER),
            owner_key=_env_optional("JANUS_OWNER_KEY"),
            target_key=_env_optional("JANUS_TARGET_KEY"),
            contract_address=_env_optional("JANUS_CONTRACT_ADDRESS"),
            memory_db=_env("JANUS_MEMORY_DB", DEFAULT_MEMORY_DB),
            solc_version=_env("JANUS_SOLC_VERSION", DEFAULT_SOLC),
            verify_contract_code=_env("JANUS_VERIFY_CODE", "1") != "0",
        )

    def deployed_contract_path(self) -> Path:
        db = Path(self.memory_db).expanduser()
        parent = db.parent
        parent.mkdir(parents=True, exist_ok=True)
        return parent / "janus_deployed.json"
