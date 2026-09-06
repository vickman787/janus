"""Render the real Janus UI against live Base mainnet data.

No test EVM, no simulated state. Four genuine onchain situations are
photographed, each backed by real transactions on Base mainnet:

  op_067888a08e        completed. the ownership transfer finished.
  op_demo_consistent   paused at pending acceptance. consistent with the chain.
  op_demo_mismatch     pending owner hijacked while Janus was offline. refused.
  op_nonexistent       no Sibyl record at all, pointed at the mismatch contract.

The refused and mismatch pages prove the same thing from two angles: the chain
is readable, the execution context is gone or stale, so Janus does not act.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import uvicorn

from janus.config import Config
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
            "--virtual-time-budget=5000",
            f"--screenshot={out}",
            url,
        ],
        capture_output=True,
        timeout=120,
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    browser = find_browser()

    config = Config.from_env()
    mismatch_contract = "0xE8a50d1AaC54f0A83770fc02C583C61715d83775"
    app = create_app(config)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    time.sleep(0.8)

    shots = [
        ("janus_mainnet_completed.png", "/?op=op_067888a08e", 980),
        ("janus_mainnet_consistent.png", "/?op=op_demo_consistent", 980),
        ("janus_mainnet_mismatch.png", "/?op=op_demo_mismatch", 1020),
        ("janus_mainnet_refused.png", f"/?op=op_does_not_exist&contract={mismatch_contract}", 940),
    ]
    for name, path, height in shots:
        shoot(browser, base + path, OUT / name, height)
        print("rendered", name)

    server.should_exit = True
    thread.join(timeout=10)
    print("\nscreenshots in", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
