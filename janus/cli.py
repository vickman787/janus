"""Janus command line interface.

Useful for the demo and for the deletion test. Each command runs in its own
fresh process, which is exactly the cold start Janus is built for.
"""
from __future__ import annotations

import argparse
import json
import sys

from janus.chain import BaseChain
from janus.config import Config
from janus.executor import JanusExecutor
from janus.memory import SibylStore
from janus.views import checkpoint_view, live_view


def _components(config: Config):
    chain = BaseChain.from_config(config)
    store = SibylStore(db_path=config.memory_db)
    executor = JanusExecutor(chain, store, config)
    return chain, store, executor


def cmd_deploy(config: Config, args) -> int:
    chain, store, executor = _components(config)
    if not config.owner_key:
        print("error: JANUS_OWNER_KEY is required to deploy.", file=sys.stderr)
        return 2
    owner = args.owner or chain.address(config.owner_key)
    address, record = chain.deploy(owner, config.owner_key)
    print(json.dumps({"contract_address": address, "tx_hash": record.tx_hash, "status": record.status}, indent=2))
    return 0


def cmd_begin(config: Config, args) -> int:
    chain, store, executor = _components(config)
    target = args.target_owner or (chain.address(config.target_key) if config.target_key else None)
    if not target:
        print("error: pass --target-owner or set JANUS_TARGET_KEY.", file=sys.stderr)
        return 2
    try:
        cp = executor.begin_ownership_transfer(
            contract_address=args.contract_address or config.contract_address,
            target_owner=target,
            operation_id=args.operation_id,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out = {"operation": checkpoint_view(chain, cp), "stopped_at": "awaiting_acceptance"}
    print(json.dumps(out, indent=2))
    print(f"\ncheckpoint written to Sibyl memory. operation id: {cp.operation_id}", file=sys.stderr)
    return 0


def cmd_status(config: Config, args) -> int:
    chain, store, executor = _components(config)
    result = executor.status(args.operation_id)
    payload = {"verdict": result.verdict.model_dump()}
    if result.checkpoint is not None:
        payload["operation"] = checkpoint_view(chain, result.checkpoint)
    if result.live is not None:
        payload["live"] = live_view(chain, result.live)
    print(json.dumps(payload, indent=2))
    return 0


def cmd_resume(config: Config, args) -> int:
    chain, store, executor = _components(config)
    result = executor.resume(args.operation_id, execute=not args.no_execute)
    payload = {"verdict": result.verdict.model_dump()}
    if result.checkpoint is not None:
        payload["operation"] = checkpoint_view(chain, result.checkpoint)
    if result.live is not None:
        payload["live"] = live_view(chain, result.live)
    if result.new_transactions:
        payload["new_transactions"] = [t.model_dump() for t in result.new_transactions]
    print(json.dumps(payload, indent=2))
    if result.verdict.status.value in ("mismatch", "no_checkpoint"):
        return 3
    return 0


def cmd_delete(config: Config, args) -> int:
    chain, store, executor = _components(config)
    removed = store.delete_checkpoint(args.operation_id)
    if not removed:
        print(f"operation {args.operation_id} not found in Sibyl memory.", file=sys.stderr)
        return 1
    print(f"deleted checkpoint {args.operation_id} from Sibyl memory.")
    return 0


def cmd_list(config: Config, args) -> int:
    chain, store, executor = _components(config)
    for cp in store.list_checkpoints():
        print(json.dumps(checkpoint_view(chain, cp), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="janus", description="Restart safe onchain execution agent on Base mainnet.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("deploy", help="Deploy the demo Ownable2Step contract.")
    p.add_argument("--owner", help="initial owner address (defaults to JANUS_OWNER_KEY address)")
    p.set_defaults(fn=cmd_deploy)

    p = sub.add_parser("begin", help="Begin a transfer, checkpoint it, and stop.")
    p.add_argument("--contract-address", help="Ownable2Step contract address")
    p.add_argument("--target-owner", help="target owner address")
    p.add_argument("--operation-id", help="optional explicit operation id")
    p.set_defaults(fn=cmd_begin)

    p = sub.add_parser("status", help="Show remembered and live state for an operation.")
    p.add_argument("operation_id")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("resume", help="Revalidate against Base and continue if safe.")
    p.add_argument("operation_id")
    p.add_argument("--no-execute", action="store_true", help="revalidate and report only")
    p.set_defaults(fn=cmd_resume)

    p = sub.add_parser("delete", help="Delete a checkpoint from Sibyl memory (deletion test).")
    p.add_argument("operation_id")
    p.set_defaults(fn=cmd_delete)

    p = sub.add_parser("list", help="List checkpoints in Sibyl memory.")
    p.set_defaults(fn=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config.from_env()
    try:
        return args.fn(config, args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
