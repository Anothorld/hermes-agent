"""kol-ops-bridge scripts package.

Marks the directory as importable so :mod:`kol_bridge_tool` can pull in
the subcommand modules whether it's invoked via
``python -m plugins.kol_ops_bridge.scripts.kol_bridge_tool`` or directly
as a file path (the file does its own ``sys.path`` shim in that case).
"""
