"""Render mission control pages against live Base mainnet data."""
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
    raise RuntimeError("no chromium browser found")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def shoot(browser: str, url: str, out: Path, height: int = 980) -> None:
    subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size=1500,{height}",
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
        ("mc_dashboard.png", "/", 900),
        ("mc_operations.png", "/operations", 900),
        ("mc_new.png", "/operations/new", 760),
        ("mc_memory.png", "/memory", 900),
        ("mc_status.png", "/status", 900),
        ("mc_cockpit_completed.png", "/operations/op_067888a08e", 980),
        ("mc_cockpit_consistent.png", "/operations/op_demo_consistent", 980),
        ("mc_cockpit_mismatch.png", "/operations/op_demo_mismatch", 1020),
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
