"""Render the read only showcase exactly as Vercel will serve it.

No keys. Seeded memory. Live mainnet reads. This is what judges see at the
hosted link.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "brand" / "out"
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def load_app():
    """Load the Vercel entrypoint exactly as Vercel imports it."""
    spec = importlib.util.spec_from_file_location("vercel_app", ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


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


def shoot(browser: str, url: str, out: Path, height: int = 900) -> None:
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

    # Simulate Vercel: read only, no keys, tmp memory, repo seed.
    os.environ["JANUS_READ_ONLY"] = "1"
    os.environ["JANUS_MEMORY_DB"] = os.path.join(os.environ["TEMP"], "janus_ro_shot.db")
    os.environ["JANUS_SEED_FILE"] = str(ROOT / "seed" / "operations.json")
    os.environ["JANUS_OWNER_KEY"] = ""
    os.environ["JANUS_TARGET_KEY"] = ""
    for ext in ("", "-wal", "-shm"):
        Path(os.environ["JANUS_MEMORY_DB"] + ext).unlink(missing_ok=True)

    # fresh app load so env is read at create_app time
    app = load_app()

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
        ("ro_dashboard.png", "/", 900),
        ("ro_operations.png", "/operations", 900),
        ("ro_cockpit_completed.png", "/operations/op_067888a08e", 1000),
        ("ro_cockpit_consistent.png", "/operations/op_demo_consistent", 1000),
        ("ro_cockpit_mismatch.png", "/operations/op_demo_mismatch", 1040),
        ("ro_memory.png", "/memory", 900),
        ("ro_status.png", "/status", 900),
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
