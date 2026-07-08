"""Internal package for kol-discovery-rpa.

Note: the parent directory ``kol-discovery-rpa`` has hyphens, so Python
cannot import it as a regular package (``plugins.kol_discovery_rpa``).
All internal modules add their own directory to ``sys.path`` and import
siblings by bare module name (``import errors``, not
``from plugins.kol_discovery_rpa.internal import errors``).
This matches the pattern used by ``kol-bridge-agent-guard``.
"""
