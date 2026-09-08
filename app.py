"""Vercel entrypoint for the Janus read only showcase.

Vercel's Python runtime imports the `app` object from this file. Read only is
forced, memory is seeded from the committed snapshot, and the sqlite file lives
under the writable temp dir for the life of one warm instance.

If startup fails, a diagnostic app is served that prints the traceback so the
real cause is visible in the browser instead of a generic 500.
"""
from __future__ import annotations

import io
import os
import tempfile
import traceback

os.environ.setdefault("JANUS_READ_ONLY", "1")
os.environ.setdefault("JANUS_MEMORY_DB", os.path.join(tempfile.gettempdir(), "janus.db"))
os.environ.setdefault("JANUS_SEED_FILE", os.path.join(os.getcwd(), "seed", "operations.json"))


def _build() -> "object":
    from fastapi import FastAPI, Request
    from fastapi.responses import PlainTextResponse

    try:
        from janus.config import Config
        from janus.web import create_app

        return create_app(Config.from_env())
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
