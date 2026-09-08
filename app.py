"""Vercel entrypoint. Runs the Janus read only showcase natively.

Vercel's Python runtime imports the `app` object from this file. Read only is
forced, memory is seeded from the committed snapshot, and the sqlite file lives
under the writable temp dir for the life of one warm instance. No signing keys
are present, which the app enforces.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("JANUS_READ_ONLY", "1")
os.environ.setdefault("JANUS_MEMORY_DB", os.path.join(tempfile.gettempdir(), "janus.db"))
os.environ.setdefault("JANUS_SEED_FILE", os.path.join(os.getcwd(), "seed", "operations.json"))

from janus.config import Config  # noqa: E402
from janus.web import create_app  # noqa: E402

app = create_app(Config.from_env())
