"""Conftest for kol-discovery-rpa tests.

The plugin directory has hyphens (kol-discovery-rpa), so Python cannot
import it as a regular package. We add the internal/ directory to
``sys.path`` so test modules can import siblings by bare name.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
INTERNAL_DIR = str(PLUGIN_ROOT / "internal")
if INTERNAL_DIR not in sys.path:
    sys.path.insert(0, INTERNAL_DIR)
