"""Upload local files to QuickCEP CDN via quickcep_cli."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from profile_refs import quickcep_skill_dir


def upload_file_to_cdn(
    file_path: Path | str,
    *,
    feature: str = "email",
    timeout: int = 120,
) -> dict[str, Any]:
    """Upload a file and return attachment-ready dict with fileName, fileSize, url."""
    path = Path(file_path)
    if not path.is_file():
        return {"ok": False, "error": f"file not found: {path}"}

    cli = quickcep_skill_dir() / "scripts" / "quickcep_cli.py"
    if not cli.is_file():
        return {"ok": False, "error": f"quickcep_cli not found: {cli}"}

    plugin_root = Path(__file__).resolve().parent
    env = os.environ.copy()
    env.setdefault("CS_OPS_BRIDGE_PLUGIN_DIR", str(plugin_root))

    proc = subprocess.run(
        [sys.executable, str(cli), "upload-file", str(path), "--feature", feature],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cli.parent.parent),
        env=env,
    )
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": proc.stderr or proc.stdout or "upload-file failed",
            "exit_code": proc.returncode,
        }
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid upload-file JSON", "stdout": proc.stdout}

    url = str(data.get("url") or "")
    if not url:
        return {"ok": False, "error": "upload-file returned empty url", "payload": data}

    file_name = str(data.get("fileName") or path.name)
    file_size = int(data.get("fileSize") or path.stat().st_size)
    attachment = {"fileName": file_name, "fileSize": file_size, "url": url}
    return {"ok": True, "url": url, "attachment": attachment, "raw": data}
