"""Render real Janus UI screenshots for every verdict state.

Runs the actual FastAPI app against an in process EVM, drives real operations
through the real executor, and screenshots the live page with headless Chrome.
Nothing here is mocked HTML: every pixel comes from the shipped app.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from eth_account import Account
from eth_tester import EthereumTester
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from janus.chain import BaseChain
from janus.config import Config
from janus.executor import JanusExecutor
from janus.memory import SibylStore
from janus.web import create_app

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "brand" / "out"
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    found = shutil.which("chrome") or shutil.which("msedge")
    if found:
        return found
    raise RuntimeError("no chromium browser found for screenshots")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def shoot(browser: str, url: str, out: Path, height: int = 900) -> None:
    subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size=1440,{height}",
            "--virtual-time-budget=4000",
            f"--screenshot={out}",
            url,
        ],
        capture_output=True,
        timeout=120,
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    browser = find_browser()
    tmp = Path(tempfile.mkdtemp(prefix="janus_shots_"))

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

    chain = BaseChain.from_provider(w3, explorer_url="https://basescan.org")
    config = Config(
        rpc_url="in_process",
        chain_id=chain.chain_id,
        explorer_url="https://basescan.org",
        owner_key=owner.key.hex(),
        target_key=target.key.hex(),
        memory_db=str(tmp / "memory.db"),
    )
    store = SibylStore(db_path=config.memory_db)
    executor = JanusExecutor(chain, store, config)

    # Operation 1: reaches awaiting acceptance. this is the CONSISTENT shot.
    addr1, _ = chain.deploy(owner.address, owner.key.hex())
    cp1 = executor.begin_ownership_transfer(contract_address=addr1, target_owner=target.address)

    # Operation 2: hijacked while offline. this is the MISMATCH shot.
    addr2, _ = chain.deploy(owner.address, owner.key.hex())
    cp2 = executor.begin_ownership_transfer(contract_address=addr2, target_owner=target.address)
    chain.transfer_ownership(addr2, stranger.address, owner.key.hex())

    # Operation 3: checkpoint deleted. this is the NO CHECKPOINT shot.
    addr3, _ = chain.deploy(owner.address, owner.key.hex())
    cp3 = executor.begin_ownership_transfer(contract_address=addr3, target_owner=target.address)
    config.contract_address = addr3
    store.delete_checkpoint(cp3.operation_id)

    app = create_app(config, chain=chain, store=store)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        if server.started:
            break
        time.sleep(0.1)
    time.sleep(0.6)

    shots = [
        ("janus_setup.png", "/", 820),
        ("janus_consistent.png", f"/?op={cp1.operation_id}", 980),
        ("janus_mismatch.png", f"/?op={cp2.operation_id}", 1020),
        ("janus_refused.png", f"/?op={cp3.operation_id}", 940),
    ]
    for name, path, height in shots:
        shoot(browser, base + path, OUT / name, height)
        print("rendered", name)

    # Complete operation 1 so we can also capture the finished state.
    executor.resume(cp1.operation_id, execute=True)
    shoot(browser, base + f"/?op={cp1.operation_id}", OUT / "janus_completed.png", 980)
    print("rendered janus_completed.png")

    server.should_exit = True
    thread.join(timeout=10)
    store.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nscreenshots in", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
