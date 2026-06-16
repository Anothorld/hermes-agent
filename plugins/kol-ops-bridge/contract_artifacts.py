"""Contract artifact paths, formal filenames, and operator preview helpers."""

from __future__ import annotations

import datetime as _dt
import html
import os
import re
import shutil
from pathlib import Path
from typing import Any, Mapping, Optional

_LEGACY_BASENAME_RE = re.compile(r"^\d+_\d{8}\.docx$", re.IGNORECASE)
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def contracts_root(*, hermes_home: Path | None = None) -> Path:
    """Return the canonical on-disk contracts directory.

    Aligns with ``cal.db_path()``: when ``HERMES_KOL_OPS_CAL_DB`` is set
    (Console ``start.sh`` default), contracts live beside that file under
    ``.../kol-ops-bridge/contracts/``, not under a profile-scoped path.
    """
    override = os.environ.get("HERMES_KOL_OPS_CONTRACTS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    cal_db = os.environ.get("HERMES_KOL_OPS_CAL_DB", "").strip()
    if cal_db:
        return (Path(cal_db).expanduser().resolve().parent / "contracts").resolve()
    if hermes_home is not None:
        return (hermes_home / "kol-ops-bridge" / "contracts").resolve()
    hermes = os.environ.get("HERMES_HOME", "").strip()
    if hermes:
        return (Path(hermes).expanduser() / "kol-ops-bridge" / "contracts").resolve()
    return (Path.home() / ".hermes" / "kol-ops-bridge" / "contracts").resolve()


def sanitize_filename_part(value: str, *, max_len: int = 48) -> str:
    """Make one path segment safe for contract filenames."""
    cleaned = _UNSAFE_FILENAME_RE.sub("_", (value or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    if not cleaned:
        return "Unknown"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip("._")
    return cleaned


def infer_product_sku(
    *,
    fields: Mapping[str, Any] | None = None,
    campaign_id: str | None = None,
    facts: Mapping[str, Any] | None = None,
) -> str:
    """Best-effort SKU token for filenames."""
    if facts:
        locked = facts.get("offer.sku_locked") or facts.get("offer.proposed_skus")
        if isinstance(locked, str) and locked.strip():
            return locked.strip()
        if isinstance(locked, list) and locked:
            first = locked[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
    if fields:
        product = fields.get("product") or {}
        if isinstance(product, dict):
            specs = str(product.get("specs") or "")
            match = re.search(r"\b([A-Z]{2,}\d{3,}[A-Z0-9-]*)\b", specs)
            if match:
                return match.group(1)
    if campaign_id:
        head = campaign_id.split("-", 1)[0].strip()
        if head:
            return head
        return campaign_id
    return "Campaign"


def build_contract_filename(
    *,
    influencer_full_name: str,
    product_sku: str,
    when: _dt.date | None = None,
) -> str:
    """Formal operator-facing contract filename."""
    name_part = sanitize_filename_part(influencer_full_name or "Creator")
    sku_part = sanitize_filename_part(product_sku or "Campaign")
    day = (when or _dt.date.today()).strftime("%Y%m%d")
    return f"POVISON_Influencer_Agreement_{name_part}_{sku_part}_{day}.docx"


def build_contract_output_path(
    *,
    env: str,
    campaign_id: str,
    fields: Mapping[str, Any],
    facts: Mapping[str, Any] | None = None,
    when: _dt.date | None = None,
    hermes_home: Path | None = None,
) -> Path:
    """Compute the absolute output path for a rendered contract docx."""
    influencer = fields.get("influencer") or {}
    full_name = ""
    if isinstance(influencer, dict):
        full_name = str(influencer.get("full_name") or "")
    sku = infer_product_sku(fields=fields, campaign_id=campaign_id, facts=facts)
    filename = build_contract_filename(
        influencer_full_name=full_name,
        product_sku=sku,
        when=when,
    )
    root = contracts_root(hermes_home=hermes_home)
    return (root / env.upper() / campaign_id / filename).resolve()


def resolve_contract_path(raw_path: str | Path) -> Path:
    """Resolve and validate that ``raw_path`` lives under ``contracts_root()``."""
    path = Path(str(raw_path or "").strip()).expanduser().resolve()
    root = contracts_root()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"contract path must be under {root}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"contract file not found: {path}")
    return path


def is_legacy_contract_basename(name: str) -> bool:
    return bool(_LEGACY_BASENAME_RE.match(name or ""))


def ensure_formal_contract_path(
    path: Path,
    *,
    fields: Mapping[str, Any] | None = None,
    campaign_id: str | None = None,
    facts: Mapping[str, Any] | None = None,
) -> Path:
    """Rename legacy ``{identity_id}_{date}.docx`` files to formal names."""
    resolved = resolve_contract_path(path)
    if not is_legacy_contract_basename(resolved.name):
        return resolved
    target = build_contract_output_path(
        env=resolved.parent.parent.name,
        campaign_id=resolved.parent.name if campaign_id is None else campaign_id,
        fields=fields or {},
        facts=facts,
    )
    if target == resolved:
        return resolved
    if target.exists():
        return target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(resolved), str(target))
    return target.resolve()


def display_name_for_path(path: Path) -> str:
    """Human-readable attachment label (underscores → spaces for legacy names)."""
    name = path.name
    if name.startswith("POVISON_Influencer_Agreement_"):
        core = name[len("POVISON_Influencer_Agreement_") :]
        if core.lower().endswith(".docx"):
            core = core[: -len(".docx")]
        return "POVISON Influencer Agreement — " + core.replace("_", " ") + ".docx"
    return name


def _mammoth_preview_html(path: Path) -> str | None:
    """Convert docx to styled HTML via mammoth (best-effort; optional dependency)."""
    try:
        import mammoth
    except ImportError:
        return None
    with path.open("rb") as fh:
        result = mammoth.convert_to_html(fh)
    body = (result.value or "").strip()
    if not body:
        return None
    return (
        '<div class="contract-docx-preview contract-mammoth-preview">'
        + body
        + "</div>"
    )


def _paragraph_alignment_style(paragraph: Any) -> str:
    try:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        return ""
    alignment = paragraph.paragraph_format.alignment
    if alignment == WD_ALIGN_PARAGRAPH.CENTER:
        return "text-align:center;"
    if alignment == WD_ALIGN_PARAGRAPH.RIGHT:
        return "text-align:right;"
    if alignment == WD_ALIGN_PARAGRAPH.JUSTIFY:
        return "text-align:justify;"
    return ""


def _paragraph_tag(paragraph: Any) -> str:
    style_name = ""
    try:
        style_name = (paragraph.style.name or "").lower()
    except Exception:  # noqa: BLE001
        pass
    if "heading 1" in style_name or style_name == "title":
        return "h1"
    if "heading 2" in style_name:
        return "h2"
    if "heading 3" in style_name:
        return "h3"
    return "p"


def _runs_to_html(paragraph: Any) -> str:
    chunks: list[str] = []
    for run in paragraph.runs:
        text = html.escape(run.text)
        if not text:
            continue
        if run.bold:
            text = f"<strong>{text}</strong>"
        if run.italic:
            text = f"<em>{text}</em>"
        if run.underline:
            text = f"<u>{text}</u>"
        chunks.append(text)
    if chunks:
        return "".join(chunks)
    return html.escape(paragraph.text)


def _table_to_html(table: Any) -> str:
    parts = ['<table class="contract-table">']
    for row in table.rows:
        parts.append("<tr>")
        for cell in row.cells:
            cell_html = "".join(
                _runs_to_html(paragraph)
                for paragraph in cell.paragraphs
                if paragraph.text.strip()
            )
            parts.append(f"<td>{cell_html or '&nbsp;'}</td>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _iter_block_items(document: Any) -> list[Any]:
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    blocks: list[Any] = []
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            blocks.append(Paragraph(child, document))
        elif isinstance(child, CT_Tbl):
            blocks.append(Table(child, document))
    return blocks


def _python_docx_preview_html(path: Path) -> str:
    """Fallback HTML preview preserving block order and inline formatting."""
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise RuntimeError(
            "python-docx is required for contract preview; "
            "install with: pip install python-docx"
        ) from exc
    doc = Document(str(path))
    parts = ['<div class="contract-docx-preview">']
    for block in _iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            tag = _paragraph_tag(block)
            align = _paragraph_alignment_style(block)
            style_attr = f' style="{align}"' if align else ""
            parts.append(f"<{tag}{style_attr}>{_runs_to_html(block)}</{tag}>")
        elif isinstance(block, Table):
            parts.append(_table_to_html(block))
    parts.append("</div>")
    return "\n".join(parts)


def docx_to_preview_html(path: Path) -> str:
    """Render HTML preview for CLI/API fallback (Console uses docx-preview client-side)."""
    mammoth_html = _mammoth_preview_html(path)
    if mammoth_html:
        return mammoth_html
    return _python_docx_preview_html(path)


def render_contract_file(
    *,
    template_path: Path,
    output_path: Path,
    fields: Mapping[str, Any],
) -> Path:
    """Render docx via ``scripts/render_contract.py`` (shared with CLI/tests)."""
    import importlib.util

    script = Path(__file__).resolve().parent / "scripts" / "render_contract.py"
    spec = importlib.util.spec_from_file_location(
        "kol_ops_bridge_render_contract",
        script,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load render_contract from {script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.render(template_path, output_path, fields)


def resolve_contract_for_identity(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    facts: Mapping[str, Any],
    draft_attachments: Optional[list[Any]] = None,
) -> tuple[Path, str]:
    """Pick contract path from facts/draft attachments and normalize filename."""
    candidates: list[str] = []
    artifact = facts.get("offer.contract_artifact_path")
    if isinstance(artifact, str) and artifact.strip():
        candidates.append(artifact.strip())
    for item in draft_attachments or []:
        if isinstance(item, str) and item.strip():
            candidates.append(item.strip())
    if not candidates:
        raise FileNotFoundError(
            f"no contract artifact for identity={identity_id} campaign={campaign_id}"
        )
    path = resolve_contract_path(candidates[0])
    formal = ensure_formal_contract_path(
        path,
        campaign_id=campaign_id,
        facts=facts,
    )
    return formal, display_name_for_path(formal)
