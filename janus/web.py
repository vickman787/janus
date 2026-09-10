"""FastAPI app for Janus. Mission control pages plus the JSON API.

Pages (server rendered, one per route):
  /                     dashboard
  /operations           operations index with Sibyl full text search
  /operations/new       start Process A or restore Process B
  /operations/{id}      the operation cockpit, remembered versus live
  /memory               memory explorer over the Sibyl tiers
  /status               network guard and settings

The JSON API under /api/* backs the pages and the demo. The API never accepts a
blind continuation: every resume goes through the reconciler.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from janus.chain import BaseChain
from janus.config import Config
from janus.executor import JanusExecutor
from janus.memory import SibylStore
from janus.models import LiveChainState, VerdictStatus
from janus.views import checkpoint_view, live_view

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


class DeployBody(BaseModel):
    initial_owner: str | None = None


class BeginBody(BaseModel):
    contract_address: str | None = None
    target_owner: str | None = None
    operation_id: str | None = None


class ResumeBody(BaseModel):
    execute: bool = True


# ------------------------------------------------------------------ helpers
def verdict_view(chain: BaseChain, result) -> dict:
    verdict = result.verdict
    out: dict = {
        "operation_id": (result.checkpoint.operation_id if result.checkpoint else None),
        "verdict": {
            "status": verdict.status.value,
            "next_action": verdict.next_action,
            "reasons": verdict.reasons,
            "message": verdict.message,
        },
        "operation": None,
        "live": None,
    }
    if result.checkpoint is not None:
        out["operation"] = checkpoint_view(chain, result.checkpoint)
    if result.live is not None:
        out["live"] = live_view(chain, result.live)
    return out


def _deploy(chain: BaseChain, config: Config, initial_owner: str | None = None) -> dict:
    if not config.owner_key:
        raise HTTPException(400, "JANUS_OWNER_KEY is required to deploy.")
    owner = initial_owner or chain.address(config.owner_key)
    address, record = chain.deploy(owner, config.owner_key)
    path = config.deployed_contract_path()
    path.write_text(json.dumps({"address": address, "tx_hash": record.tx_hash}), encoding="utf-8")
    log.info("deployed demo contract %s tx %s", address, record.tx_hash)
    return {
        "contract_address": address,
        "tx_hash": record.tx_hash,
        "tx_url": chain.tx_url(record.tx_hash),
        "owner": owner,
        "block_number": record.block_number,
    }


def _contract_address(config: Config, provided: str | None = None) -> str:
    if provided:
        return provided
    if config.contract_address:
        return config.contract_address
    path = config.deployed_contract_path()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("address", "")
    return ""


def _cfg_view(chain: BaseChain, config: Config) -> dict:
    owner = chain.address(config.owner_key) if config.owner_key else None
    target = chain.address(config.target_key) if config.target_key else None
    connected = False
    chain_id = None
    block = None
    try:
        connected = chain.is_connected
        chain_id = chain.chain_id
        block = chain.block_number()
    except Exception:
        pass
    return {
        "chain_id": chain_id,
        "expected_chain_id": config.chain_id,
        "mainnet": chain_id == 8453 if chain_id is not None else None,
        "rpc_url": config.rpc_url,
        "explorer_url": config.explorer_url,
        "connected": connected,
        "block_number": block,
        "owner_address": owner,
        "target_address": target,
        "has_owner_key": bool(config.owner_key),
        "has_target_key": bool(config.target_key),
        "contract_address": _contract_address(config),
        "memory_db": config.memory_db,
    }


def _base_ctx(config: Config) -> dict:
    return {"active": None, "banner": None, "read_only": config.read_only}


def _seed_store(config: Config, store: SibylStore) -> None:
    """Import a committed snapshot when the store is empty and a seed exists.

    Used by the read only Vercel deployment, where the filesystem is ephemeral
    and cannot carry a persistent SQLite file. The seed ships real checkpoints
    taken from the operator machine; live state is still read fresh from Base.
    """
    if config.read_only and store.is_empty() and config.seed_file:
        path = Path(config.seed_file)
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            imported = store.import_state(state)
            log.info("seeded %s checkpoints from %s", imported, path)


def _status_badge(cp_view: dict) -> str:
    if cp_view["current_step"] == "completed":
        return "done"
    if cp_view["failed_steps"]:
        return "mismatch"
    if cp_view["current_step"] == "awaiting_acceptance":
        return "wait"
    return "wait"


# ------------------------------------------------------------------- factory
def create_app(
    config: Config | None = None,
    *,
    chain: BaseChain | None = None,
    store: SibylStore | None = None,
) -> FastAPI:
    if config is None:
        config = Config.from_env()
    chain = chain if chain is not None else BaseChain.from_config(config)
    store = store if store is not None else SibylStore(db_path=config.memory_db)
    _seed_store(config, store)
    executor = JanusExecutor(chain, store, config)

    app = FastAPI(title="Janus")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def render(request: Request, name: str, ctx: dict, active: str) -> HTMLResponse:
        ctx = dict(ctx)
        ctx["active"] = active
        return templates.TemplateResponse(request, name, ctx)

    def op_cards() -> list[dict]:
        checkpoints = store.list_checkpoints()
        checkpoints.sort(key=lambda c: c.updated_at, reverse=True)
        cards = []
        for cp in checkpoints:
            view = checkpoint_view(chain, cp)
            view["status_badge"] = _status_badge(view)
            cards.append(view)
        return cards

    # ------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    def page_dashboard(request: Request):
        cards = op_cards()
        ctx = _base_ctx(config)
        ctx.update(
            {
                "recent": cards[:8],
                "ops_count": len(cards),
                "done_count": sum(1 for c in cards if c["current_step"] == "completed"),
                "wait_count": sum(1 for c in cards if c["current_step"] == "awaiting_acceptance"),
                "refuse_count": sum(1 for c in cards if c["failed_steps"]),
            }
        )
        return render(request, "dashboard.html", ctx, "dashboard")

    @app.get("/operations", response_class=HTMLResponse)
    def page_operations(request: Request, q: str = "", op: str = ""):
        if op:
            return RedirectResponse(f"/operations/{op}", status_code=303)
        ctx = _base_ctx(config)
        if q:
            hits = store.search(q, limit=50)
            ids = [h["name"] for h in hits if h.get("name")]
            keep = []
            for cp in store.list_checkpoints():
                if cp.operation_id in ids:
                    keep.append(cp)
            keep.sort(key=lambda c: c.updated_at, reverse=True)
            cards = [checkpoint_view(chain, c) for c in keep]
            ctx["q"] = q
        else:
            cards = op_cards()
            ctx["q"] = ""
        for c in cards:
            c["status_badge"] = _status_badge(c)
        ctx["ops"] = cards
        if request.query_params.get("msg") == "deleted":
            ctx["banner"] = {"text": "Checkpoint deleted from Sibyl memory. That is the deletion test: resume now refuses.", "kind": "ok"}
        return render(request, "operations.html", ctx, "operations")

    @app.get("/operations/new", response_class=HTMLResponse)
    def page_new(request: Request):
        ctx = _base_ctx(config)
        q = request.query_params
        if q.get("deployed"):
            ctx["contract_address"] = q.get("deployed")
            ctx["banner"] = {"text": f"Contract deployed at {q.get('deployed')}", "kind": "ok"}
        elif q.get("error"):
            ctx["banner"] = {"text": q.get("error"), "kind": "err"}
        else:
            ctx["banner"] = None
        ctx["contract_address"] = ctx.get("contract_address") or _contract_address(config) or ""
        return render(request, "new_operation.html", ctx, "operations")

    @app.post("/operations/deploy")
    def page_deploy(request: Request):
        if config.read_only:
            return RedirectResponse("/operations/new?error=read+only+deployment.+no+signing+keys.", status_code=303)
        try:
            result = _deploy(chain, config)
        except Exception as exc:
            return RedirectResponse(f"/operations/new?error={_url_quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/operations/new?deployed={result['contract_address']}", status_code=303)

    @app.post("/operations/begin")
    def page_begin(request: Request, contract_address: str = "", operation_id: str = ""):
        if config.read_only:
            return RedirectResponse("/operations/new?error=read+only+deployment.+no+signing+keys.", status_code=303)
        contract = contract_address.strip() or _contract_address(config) or None
        try:
            cp = executor.begin_ownership_transfer(
                contract_address=contract,
                operation_id=operation_id.strip() or None,
            )
        except Exception as exc:
            return RedirectResponse(f"/operations/new?error={_url_quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/operations/{cp.operation_id}?msg=checkpointed", status_code=303)

    @app.get("/operations/{operation_id}", response_class=HTMLResponse)
    def page_cockpit(request: Request, operation_id: str):
        result = executor.status(operation_id, contract_address=_contract_address(config) or None)
        ctx = _base_ctx(config)
        q = request.query_params
        view = verdict_view(chain, result)
        if view["operation"] is None:
            if not q.get("error"):
                ctx["banner"] = {"text": view["verdict"]["message"], "kind": "err"}
            view["operation"] = {
                "operation_id": operation_id,
                "operation_type": "ownership_transfer",
                "contract_address": _contract_address(config) or "unknown",
                "current_step": "no_checkpoint",
                "current_step_label": "No checkpoint",
                "steps": [],
                "expected_current_owner": "",
                "target_owner": "",
                "chain_id": config.chain_id,
                "last_verified_block": None,
                "transaction_hashes": [],
                "failed_steps": [],
                "contract_url": "",
            }
        elif q.get("msg") == "continued":
            ctx["banner"] = {"text": "Continuation executed and verified onchain.", "kind": "ok"}
        elif q.get("error"):
            ctx["banner"] = {"text": q.get("error"), "kind": "err"}
        ctx["op"] = view["operation"]
        ctx["verdict"] = view["verdict"]
        if view["live"] is None:
            ctx["live"] = {
                "contract_address": ctx["op"]["contract_address"],
                "chain_id": None,
                "owner": "unreadable",
                "pending_owner": "unreadable",
                "block_number": None,
                "runtime_code_hash": None,
            }
        else:
            ctx["live"] = view["live"]
        return render(request, "cockpit.html", ctx, "operations")

    @app.post("/operations/{operation_id}/continue")
    def page_continue(request: Request, operation_id: str):
        if config.read_only:
            return RedirectResponse(f"/operations/{operation_id}?error=read+only+deployment.+no+signing+keys.", status_code=303)
        try:
            result = executor.resume(operation_id, execute=True)
            if result.verdict.status in (VerdictStatus.MISMATCH, VerdictStatus.NO_CHECKPOINT):
                raise RuntimeError(result.verdict.message)
        except Exception as exc:
            return RedirectResponse(f"/operations/{operation_id}?error={_url_quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/operations/{operation_id}?msg=continued", status_code=303)

    @app.post("/operations/{operation_id}/delete")
    def page_delete(request: Request, operation_id: str):
        store.delete_checkpoint(operation_id)
        return RedirectResponse("/operations?msg=deleted", status_code=303)

    @app.post("/operations/{operation_id}/refresh")
    def page_refresh(request: Request, operation_id: str):
        return RedirectResponse(f"/operations/{operation_id}", status_code=303)

    @app.get("/memory", response_class=HTMLResponse)
    def page_memory(request: Request, q: str = ""):
        ctx = _base_ctx(config)
        cards = op_cards()
        ctx["checkpoints"] = cards
        ctx["tiers"] = "entities . state . journal . search"
        ctx["q"] = q
        search_rows: list[dict] = []
        hit_verdict = None
        if q:
            results = store.search(q, limit=50)
            verdict = getattr(results, "verdict", None)
            if verdict is not None and hasattr(verdict, "code"):
                hit_verdict = verdict.code.value
            for row in results:
                rec = dict(row)
                rec.setdefault("category", "")
                rec.setdefault("name", "")
                rec.setdefault("ts", "")
                rec.setdefault("body", "")
                search_rows.append(rec)
        ctx["search_rows"] = search_rows
        ctx["hit_verdict"] = hit_verdict
        ctx["hits"] = search_rows
        try:
            events = store.read_events(limit=200)
        except Exception:
            events = []
        ctx["events"] = events
        return render(request, "memory.html", ctx, "memory")

    @app.get("/status", response_class=HTMLResponse)
    def page_status(request: Request):
        ctx = _base_ctx(config)
        cfg = _cfg_view(chain, config)
        ctx["cfg"] = cfg
        ctx["expected_chain_id"] = config.chain_id
        ctx["mem_file"] = Path(config.memory_db).name
        ctx["ops_count"] = len(store.list_checkpoints())
        return render(request, "status.html", ctx, "status")

    @app.get("/guide", response_class=HTMLResponse)
    def page_guide(request: Request):
        ctx = _base_ctx(config)
        return render(request, "guide.html", ctx, "guide")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    # ------------------------------------------------------------ api
    @app.get("/api/config")
    def config_meta():
        return _cfg_view(chain, config)

    @app.post("/api/deploy")
    def api_deploy(body: DeployBody):
        if config.read_only:
            raise HTTPException(403, "read only deployment. no signing keys.")
        try:
            return _deploy(chain, config, body.initial_owner)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc

    @app.post("/api/operations")
    def api_begin(body: BeginBody):
        if config.read_only:
            raise HTTPException(403, "read only deployment. no signing keys.")
        try:
            cp = executor.begin_ownership_transfer(
                contract_address=_contract_address(config, body.contract_address) or None,
                target_owner=body.target_owner,
                operation_id=body.operation_id,
            )
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        live_raw = chain.live_state(cp.contract_address)
        live = LiveChainState.model_validate(live_raw)
        return {"operation": checkpoint_view(chain, cp), "live": live_view(chain, live)}

    @app.get("/api/operations")
    def api_list():
        checkpoints = store.list_checkpoints()
        checkpoints.sort(key=lambda c: c.updated_at, reverse=True)
        return {
            "operations": [checkpoint_view(chain, c) for c in checkpoints],
            "count": len(checkpoints),
        }

    @app.get("/api/operations/{operation_id}")
    def api_status(operation_id: str, contract: str | None = None):
        fallback = contract or _contract_address(config) or None
        return verdict_view(chain, executor.status(operation_id, contract_address=fallback))

    @app.post("/api/operations/{operation_id}/continue")
    def api_continue(operation_id: str, body: ResumeBody):
        if config.read_only:
            raise HTTPException(403, "read only deployment. no signing keys.")
        result = executor.resume(operation_id, execute=body.execute)
        if result.verdict.status in (VerdictStatus.MISMATCH, VerdictStatus.NO_CHECKPOINT):
            raise HTTPException(409, result.verdict.message)
        return verdict_view(chain, result)

    @app.delete("/api/operations/{operation_id}")
    def api_delete(operation_id: str):
        if config.read_only:
            raise HTTPException(403, "read only deployment. no signing keys.")
        removed = store.delete_checkpoint(operation_id)
        if not removed:
            raise HTTPException(404, "operation not found in Sibyl memory")
        return {"deleted": True, "operation_id": operation_id}

    @app.get("/api/operations/{operation_id}/memory")
    def api_memory_proof(operation_id: str):
        cp = store.load_checkpoint(operation_id)
        if cp is None:
            return {"operation_id": operation_id, "exists": False}
        events = store.events_for_operation(operation_id)
        return {
            "operation_id": operation_id,
            "exists": True,
            "category": "janus.operation",
            "db_path": config.memory_db,
            "entity": checkpoint_view(chain, cp),
            "events": events,
            "event_count": len(events),
        }

    return app


def _url_quote(value: str) -> str:
    import urllib.parse

    return urllib.parse.quote(value)


def make_app() -> FastAPI:
    return create_app()
