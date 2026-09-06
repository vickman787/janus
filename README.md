# Janus

Janus is a restart safe onchain execution agent. It remembers what already happened, checks the chain to confirm that memory is still true, then safely continues an interrupted operation instead of starting over.

Janus runs on Base mainnet. Sibyl Memory is the load bearing memory layer. Live Base state is the source of truth that every continuation is checked against.

The demo operation is a two step Ownable2Step ownership transfer:

`transferOwnership(newOwner)` then `pending owner recorded onchain` then interruption then `acceptOwnership()` then `ownership verified`.

A restart between those transactions is where Janus earns its name. One face looks back at what Sibyl remembers. The other face looks forward at what Base says is true now. Janus acts only when the two agree.

## Why memory is load bearing

Janus cannot resume an operation without Sibyl. It does not keep execution state in the process, in a file, or anywhere else. The checkpoint lives only in Sibyl memory.

The litmus test: delete the Sibyl record and a fresh process has no idea which operation this is, what it intended, what it completed, or what it verified. It refuses to continue automatically.

```
NO EXECUTION CHECKPOINT FOUND. AUTOMATIC RESUME REFUSED.
```

That refusal is the proof that Sibyl changes an action, not just the wording of an answer.

## How it works

Before restart (Process A)

1. User starts an ownership transfer.
2. Janus reads `owner()` and `pendingOwner()` from Base mainnet.
3. Janus verifies the initial safety conditions. Owner must match the expected current owner. Pending owner must be zero.
4. Janus submits `transferOwnership(targetOwner)`.
5. The transaction confirms.
6. Janus verifies `pendingOwner == targetOwner`.
7. The execution checkpoint is written to Sibyl.
8. The process is terminated deliberately before acceptance.

After a fresh process starts (Process B)

1. The user supplies the same operation id.
2. Janus reads the persisted checkpoint from Sibyl.
3. The UI shows that the transfer reached the pending acceptance stage.
4. Janus does not act yet.
5. It independently reads `owner()` and `pendingOwner()` from Base mainnet.
6. It compares live state against the expected checkpoint.
7. If consistent, Janus reports that `acceptOwnership()` is the next safe action.
8. The target owner signer submits acceptance.
9. Janus waits for confirmation and verifies `owner == targetOwner`.
10. The final state is persisted to Sibyl as completed.

If live state disagrees with memory, or no checkpoint exists, Janus stops and refuses. It never blindly continues from remembered state.

## Repo layout

| Path | Role |
| --- | --- |
| `contracts/Ownable2Step.sol` | The minimal two step ownership demo contract |
| `janus/artifacts/Ownable2Step.json` | Committed ABI and bytecode, compiled with solc 0.8.28 |
| `janus/models.py` | Checkpoint schema, step names, verdicts |
| `janus/memory.py` | Sibyl persistence layer |
| `janus/chain.py` | Base reads, signing, deploy, transfers |
| `janus/reconciler.py` | Pure reconcile of remembered vs live state |
| `janus/executor.py` | Begin, resume, continue orchestration |
| `janus/web.py` | FastAPI app and JSON API |
| `janus/views.py` | UI shaping of checkpoints and live state |
| `janus/cli.py` | Command line driver for scripting and the deletion test |
| `janus/static/` | The single screen UI |
| `tests/` | Local EVM tests for every refusal and success path |
| `scripts/demo_evm.py` | Cold start demo against an in process EVM |

## Setup

Requires Python 3.12 or newer. The repo uses `uv`.

```
uv sync
```

Copy the env template and fill in your keys.

```
copy .env.example .env
```

Base mainnet is the only supported network. The env template points at `https://mainnet.base.org` and chain id `8453`. If anything in an old writeup says testnet, ignore it. Janus deploys, reads, and transacts on Base mainnet only.

The owner key signs the deploy and `transferOwnership`. The target key signs `acceptOwnership`. Keep both funded with a little ETH on Base mainnet.

Janus never writes private keys, seeds, or signing credentials to Sibyl. Keys live only in the environment.

## Run the tests locally

All tests run against an in process EVM. No testnet, no funds, no network.

```
uv run pytest
```

The suite proves the full lifecycle and every refusal path:

- Begin, checkpoint, fresh session restore, accept, and completion.
- Restore with a brand new Sibyl client and executor object (fresh process simulation).
- Memory that genuinely survives a separate OS process, exercised in `tests/test_executor.py::test_fresh_process_persistence_across_subprocess`.
- Live mismatch refusal. The pending owner changes while Janus is offline, so continuation is blocked.
- Missing memory refusal. No checkpoint means no resume.
- The deletion test. Remove the Sibyl record and automatic resume breaks.
- Web API lifecycle through the FastAPI TestClient.

```
uv run python scripts/demo_evm.py
```

prints the full cold start story end to end.

## Run the web UI locally

```
uv run uvicorn janus.web:create_app --factory --reload
```

Open `http://127.0.0.1:8000`.

Process A flow

1. Click deploy demo contract. This sends a real transaction on Base mainnet.
2. Click begin transfer and checkpoint. Janus reads Base, submits `transferOwnership`, verifies the pending owner, writes the checkpoint to Sibyl, and stops.
3. Kill the server process. The checkpoint survives in Sibyl.

Fresh Process B flow

1. Start the server again. This is a genuinely fresh process.
2. Pick the operation from the dropdown, which is populated from Sibyl memory.
3. Janus restores the checkpoint and shows it under Remembered state.
4. It revalidates against Base and shows the verdict under Live Base state.
5. If consistent, Janus shows the next safe action. Click continue to send `acceptOwnership`.
6. Janus verifies the new owner and persists completion.

## Command line

Each command runs in its own fresh process, which is the cold start Janus is built for.

```
uv run janus deploy
uv run janus begin --contract-address 0x... --target-owner 0x...
uv run janus status op_...
uv run janus resume op_...
uv run janus delete op_...
uv run janus list
```

The deletion test is two commands:

```
uv run janus resume op_...
uv run janus delete op_...
uv run janus resume op_...
```

The final resume returns refusal with `NO EXECUTION CHECKPOINT FOUND`.

## Where Sibyl is load bearing

Every checkpoint write goes through `janus/memory.py`. Every resume starts by reading from Sibyl and refuses when the record is absent. The reconciler in `janus/reconciler.py` turns the pair into a decision, and `janus/executor.py` never signs a continuation that the reconciler did not clear.

Pointers for a judge, all reachable in under two minutes:

| What | Where |
| --- | --- |
| Checkpoint written to Sibyl | `janus/memory.py:56` `save_checkpoint`, calls `set_entity` at `janus/memory.py:59` |
| Checkpoint read from Sibyl | `janus/memory.py:62` `load_checkpoint`, calls `get_entity` at `janus/memory.py:64` |
| Listing remembered operations | `janus/memory.py:69` `list_checkpoints`, calls `list_entities` |
| Active operation pointer, HOT tier | `janus/memory.py:89` `set_active` via `set_state` |
| Append only audit journal | `janus/memory.py:105` `log_event` via `write_event` |
| FTS5 search over memory | `janus/memory.py:128` `search` via `search_entities` |
| Begin flow writes checkpoint then stops | `janus/executor.py:51` |
| Resume refuses without a checkpoint | `janus/executor.py:165` |
| Reconcile remembered vs live | `janus/reconciler.py:18` |
| Final completion persisted to Sibyl | `janus/executor.py` around line 295 |
| Live reads from Base | `janus/chain.py:86` `live_state` |
| Sign and send on Base | `janus/chain.py:151` and `janus/chain.py:165` |

The deletion test in `tests/test_executor.py::test_deletion_test_removing_memory_breaks_resume` shows the exact behaviour: no Sibyl record, no resume.

## Partner stacks

Base mainnet is a verified stack and does real work. Janus reads `owner()` and `pendingOwner()` before every continuation and submits both ownership transactions onchain. Explorer links are shown for every transaction. Nothing in the proof path is simulated.

Sibyl Memory is the mandatory stack and is never counted as a bonus stack.

## What is out of scope

No multi agent system. No DAO governance. No upgradeable proxies. No treasury management. No generalized workflow engine. No autonomous scheduling. No marketplace. No payment logic. No multiple operation types. One ownership transfer operation, done well.

## Prior Work

This is an original build created for the Sibyl Labs Hackathon. It uses the Sibyl Memory SDK, the OpenZeppelin Ownable2Step pattern (reimplemented as a minimal self contained contract in `contracts/Ownable2Step.sol`), and Base mainnet. No code was copied from another hackathon submission.

## License

MIT. See `LICENSE`.
