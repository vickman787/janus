"""Base chain access for Janus: reads, signing, and two step ownership moves.

Supports a real Base mainnet HTTP RPC and an in process EVM provider so the same
code path runs against a local chain in tests. Signers are supplied as private
key hex strings and are never persisted anywhere by Janus.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from eth_account import Account
from web3 import Web3

from janus.config import ARTIFACT_PATH, DEFAULT_SOLC, ZERO_ADDRESS, Config
from janus.models import TxRecord

log = logging.getLogger(__name__)


def _load_artifact() -> dict[str, Any]:
    with open(ARTIFACT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class BaseChain:
    """Wraps a web3 instance exposing the reads and writes Janus needs."""

    def __init__(self, w3: Web3, explorer_url: str = "") -> None:
        self.w3 = w3
        self._explorer = explorer_url

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, config: Config) -> "BaseChain":
        w3 = Web3(Web3.HTTPProvider(config.rpc_url))
        return cls(w3, explorer_url=config.explorer_url)

    @classmethod
    def from_provider(cls, w3: Web3, explorer_url: str = "") -> "BaseChain":
        return cls(w3, explorer_url=explorer_url)

    # ------------------------------------------------------------------
    # Chain state
    # ------------------------------------------------------------------
    @property
    def chain_id(self) -> int:
        return self.w3.eth.chain_id

    @property
    def is_connected(self) -> bool:
        return bool(self.w3.is_connected())

    def block_number(self) -> int:
        return self.w3.eth.block_number

    def address(self, key_hex: str) -> str:
        return Account.from_key(key_hex).address

    def balance(self, address: str) -> int:
        return self.w3.eth.get_balance(address)

    def code(self, address: str) -> bytes:
        return self.w3.eth.get_code(address)

    def runtime_code_hash(self, address: str) -> str:
        return self.w3.keccak(self.code(address)).hex()

    # ------------------------------------------------------------------
    # Contract reads
    # ------------------------------------------------------------------
    def _contract(self, address: str):
        artifact = _load_artifact()
        return self.w3.eth.contract(address=Web3.to_checksum_address(address), abi=artifact["abi"])

    def owner(self, contract_address: str) -> str:
        return self._contract(contract_address).functions.owner().call()

    def pending_owner(self, contract_address: str) -> str:
        return self._contract(contract_address).functions.pendingOwner().call()

    def live_state(self, contract_address: str) -> dict[str, Any]:
        contract = self._contract(contract_address)
        return {
            "contract_address": contract_address,
            "chain_id": self.chain_id,
            "owner": contract.functions.owner().call(),
            "pending_owner": contract.functions.pendingOwner().call(),
            "block_number": self.block_number(),
            "runtime_code_hash": self.runtime_code_hash(contract_address),
        }

    def contract_is_clean(self, contract_address: str) -> bool:
        state = self.live_state(contract_address)
        return state["pending_owner"] == ZERO_ADDRESS

    # ------------------------------------------------------------------
    # Signing and sending
    # ------------------------------------------------------------------
    def _send(self, tx: dict[str, Any], signer_key: str, tag: str) -> TxRecord:
        account = Account.from_key(signer_key)
        tx.setdefault("chainId", self.chain_id)
        tx.setdefault("from", account.address)
        tx.setdefault("nonce", self.w3.eth.get_transaction_count(account.address))
        tx.setdefault("gas", 400_000)
        gas_price = self.w3.eth.gas_price
        tx.setdefault("gasPrice", gas_price)
        signed = account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        block = int(receipt["blockNumber"])
        status = int(receipt["status"])
        log.info("sent %s hash=%s status=%s block=%s", tag, tx_hash.hex(), status, block)
        return TxRecord(step=tag, tx_hash=tx_hash.hex(), status=status, block_number=block)

    def deploy(
        self,
        initial_owner: str,
        signer_key: str,
        tag: str = "deploy_ownable2step",
    ) -> tuple[str, TxRecord]:
        artifact = _load_artifact()
        contract = self.w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
        account = Account.from_key(signer_key)
        tx = contract.constructor(initial_owner).build_transaction(
            {
                "from": account.address,
                "chainId": self.chain_id,
                "nonce": self.w3.eth.get_transaction_count(account.address),
                "gas": 1_500_000,
                "gasPrice": self.w3.eth.gas_price,
            }
        )
        signed = account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        address = receipt["contractAddress"]
        record = TxRecord(
            step=tag,
            tx_hash=tx_hash.hex(),
            status=int(receipt["status"]),
            block_number=int(receipt["blockNumber"]),
        )
        log.info("deployed contract %s in %s", address, tx_hash.hex())
        return address, record

    def transfer_ownership(self, contract_address: str, new_owner: str, signer_key: str) -> TxRecord:
        contract = self._contract(contract_address)
        account = Account.from_key(signer_key)
        tx = contract.functions.transferOwnership(Web3.to_checksum_address(new_owner)).build_transaction(
            {
                "from": account.address,
                "chainId": self.chain_id,
                "nonce": self.w3.eth.get_transaction_count(account.address),
                "gas": 400_000,
                "gasPrice": self.w3.eth.gas_price,
            }
        )
        return self._send(tx, signer_key, "transfer_ownership")

    def accept_ownership(self, contract_address: str, signer_key: str) -> TxRecord:
        contract = self._contract(contract_address)
        account = Account.from_key(signer_key)
        tx = contract.functions.acceptOwnership().build_transaction(
            {
                "from": account.address,
                "chainId": self.chain_id,
                "nonce": self.w3.eth.get_transaction_count(account.address),
                "gas": 400_000,
                "gasPrice": self.w3.eth.gas_price,
            }
        )
        return self._send(tx, signer_key, "accept_ownership")

    # ------------------------------------------------------------------
    # Explorer links
    # ------------------------------------------------------------------
    def tx_url(self, tx_hash: str) -> str:
        if not self._explorer:
            return ""
        return f"{self._explorer}/tx/{tx_hash}"

    def address_url(self, address: str) -> str:
        if not self._explorer:
            return ""
        return f"{self._explorer}/address/{address}"


def compile_artifact(
    source_path: Path | None = None,
    solc_version: str = DEFAULT_SOLC,
    out_path: Path = ARTIFACT_PATH,
) -> Path:
    """Compile the Ownable2Step source with py-solc-x and cache the artifact.

    The artifact is committed to the repo so deployment and tests never need a
    live solc binary. Recompile only when the source changes.
    """
    import solcx

    if source_path is None:
        source_path = Path("contracts/Ownable2Step.sol")
    if not solcx.get_installed_solc_versions():
        solcx.install_solc(solc_version)
    source = source_path.read_text(encoding="utf-8")
    compiled = solcx.compile_source(source, solc_version=solc_version, output_values=["abi", "bin"])
    key = next(k for k in compiled if k.endswith(":Ownable2Step"))
    data = compiled[key]
    artifact = {
        "solc_version": solc_version,
        "abi": data["abi"],
        "bytecode": "0x" + data["bin"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    log.info("wrote artifact to %s", out_path)
    return out_path
