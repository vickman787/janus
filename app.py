"""Vercel entrypoint for the Janus read only showcase.

Vercel's Python runtime imports the `app` object from this file. Read only is
forced, memory is seeded from the committed snapshot, and the sqlite file lives
under the writable temp dir for the life of one warm instance.

A diagnostic route and error handlers are attached so that any deployment
problem prints its cause in the browser instead of an opaque error.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import traceback

os.environ.setdefault("JANUS_READ_ONLY", "1")
os.environ.setdefault("JANUS_MEMORY_DB", os.path.join(tempfile.gettempdir(), "janus.db"))
os.environ.setdefault("JANUS_SEED_FILE", os.path.join(os.getcwd(), "seed", "operations.json"))


def _attach_diag(app) -> None:
    import fastapi
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import PlainTextResponse

    @app.get("/__diag", include_in_schema=False)
    async def diag(request: Request):
        seed = os.environ.get("JANUS_SEED_FILE", "")
        lines = [
            "JANUS DIAGNOSTIC",
            f"python   : {sys.version.split()[0]}",
            f"fastapi  : {fastapi.__version__}",
            f"cwd      : {os.getcwd()}",
            f"read_only: {os.environ.get('JANUS_READ_ONLY')}",
            f"seed_file: {seed} exists={os.path.exists(seed)}",
            f"request  : {request.url.path}",
            "routes   :",
        ]
        seen = set()
        for route in app.routes:
            methods = ",".join(sorted(getattr(route, "methods", []) or []))
            if route.path not in seen:
                seen.add(route.path)
                lines.append(f"  {methods:12s} {route.path}")
        return PlainTextResponse("\n".join(lines))

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        return PlainTextResponse(
            f"ValidationError for {request.url.path}\n{exc}",
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def on_error(request: Request, exc: Exception):
        buf = io.StringIO()
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=buf)
        return PlainTextResponse(
            f"Unhandled error for {request.url.path}\n\n{buf.getvalue()}",
            status_code=500,
        )


def _build():
    from fastapi import FastAPI, Request
    from fastapi.responses import PlainTextResponse

    try:
        from janus.config import Config
        from janus.web import create_app

        app = create_app(Config.from_env())
        _attach_diag(app)
        return app
    except Exception:
        buf = io.StringIO()
        traceback.print_exc(file=buf)
        diag = FastAPI()

        @diag.get("/{path:path}")
        @diag.post("/{path:path}")
        @diag.delete("/{path:path}")
        async def show_error(request: Request):
            return PlainTextResponse(
                "Janus startup failed.\n\n" + buf.getvalue(),
                status_code=500,
            )

        return diag


app = _build()
