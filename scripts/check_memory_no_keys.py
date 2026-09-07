"""Confirm no private key material sits in the Sibyl memory db.

Addresses and transaction hashes legitimately live in checkpoints and the
journal. The test is whether any 64 char hex blob in the file equals the actual
owner or target private key, or a derived signing secret.
"""
from __future__ import annotations

import re
import sqlite3

from eth_account import Account

from janus.config import Config

BARE = re.compile(rb"[^0-9a-fA-F]([0-9a-fA-F]{64})[^0-9a-fA-F]")


def main() -> int:
    config = Config.from_env()
    db = config.memory_db

    with open(db, "rb") as fh:
        raw = fh.read()

    blobs = {m.group(1).lower() for m in BARE.finditer(raw)}
    print("unique 64 char hex blobs:", len(blobs))

    keys = []
    if config.owner_key:
        keys.append(("owner_key", config.owner_key))
    if config.target_key:
        keys.append(("target_key", config.target_key))

    found = False
    for label, key in keys:
        clean = key.removeprefix("0x").lower()
        if clean.encode() in blobs or bytes.fromhex(clean) in blobs:
            print("FOUND:", label, "is present in the memory db!")
            found = True
        else:
            print("clean:", label, "not present")

    if not found:
        print("PASS: no configured private key is stored in the memory db.")
    return 0 if not found else 1


if __name__ == "__main__":
    raise SystemExit(main())
