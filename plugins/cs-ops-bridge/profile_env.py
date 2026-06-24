"""Load povison-cs profile .env into os.environ for bridge subprocesses."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from profile_refs import cs_profile_dir

log = logging.getLogger(__name__)

_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def load_profile_dotenv(*, override: bool = False) -> int:
    """Load profile `.env` keys into ``os.environ``. Returns count of vars set."""
    env_path = cs_profile_dir() / ".env"
    if not env_path.exists():
        return 0
    loaded = 0
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
            val = val[1:-1]
        if not override and os.environ.get(key):
            continue
        os.environ[key] = val
        loaded += 1
    if loaded:
        log.info("loaded %s vars from %s", loaded, env_path)
    return loaded
