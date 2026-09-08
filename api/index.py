"""Vercel serverless entry point for the Janus read only showcase.

Vercel runs this as a stateless function. Janus memory is seeded from the
committed snapshot so the dashboard shows real operations, and live state is
read fresh from Base mainnet on every request. No signing keys are present,
which is enforced by the read only flag in the app.
"""
from __future__ import annotations

import os
import tempfile

from mangum import Mangum

from janus.config import Config
from janus.web import create_app

os.environ.setdefault("JANUS_READ_ONLY", "1")
# Serverless filesystems are ephemeral; keep the sqlite file under the
# writable temp dir for the lifetime of one warm instance.
os.environ.setdefault("JANUS_MEMORY_DB", os.path.join(tempfile.gettempdir(), "janus.db"))
os.environ.setdefault("JANUS_SEED_FILE", os.path.join(os.getcwd(), "seed", "operations.json"))

config = Config.from_env()
app = create_app(config)
handler = Mangum(app, lifespan="off")
