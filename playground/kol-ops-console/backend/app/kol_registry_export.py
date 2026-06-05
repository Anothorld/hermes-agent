"""Export KOL registry rows to a minimal .xlsx (stdlib only)."""

from __future__ import annotations

import datetime as _dt
import sqlite3
import zipfile
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from .bridge_client import BridgeClient, BridgeError

_EXPORT_HEADERS: tuple[str, ...] = (
    "序号",
    "ID",
    "IG链接",
    "内部曾触达次数",
    "目标SPU",
    "粉丝量",
    "平均播放",
    "受众画像",
    "邮箱",
    "初邀已批准",
    "有回信",
)

_AUDIENCE_SUMMARY_FIELDS: tuple[tuple[str, str], ...] = (
    ("identity.nox_top_region", "地区"),
    ("identity.region", "地区"),
    ("identity.nox_gender_skew", "性别"),
    ("identity.nox_audience_age_distribution", "年龄"),
    ("identity.nox_audience_authenticity", "真实度"),
    ("identity.nox_audience_quality_score", "质量分"),
    ("identity.nox_audience_interests_top", "兴趣"),
)

_MAX_EXPORT_ROWS = 10_000
_PAGE_SIZE = 200


def _col_letter(index: int) -> str:
    n = index
    out = ""
    while True:
        out = chr(ord("A") + (n % 26)) + out
        n = n // 26 - 1
        if n < 0:
            break
    return out


def _cell_xml(col: int, row: int, value: Any) -> str:
    ref = f"{_col_letter(col)}{row}"
    if value is None or value == "":
        return f'<c r="{ref}"/>'
    if isinstance(value, bool):
        text = "TRUE" if value else "FALSE"
        return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'
    if isinstance(value, int) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    if isinstance(value, float):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def rows_to_xlsx_bytes(headers: list[str], rows: list[list[Any]]) -> bytes:
    """Build a one-sheet .xlsx workbook in memory."""
    sheet_rows = [headers, *rows]
    sheet_lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for r_idx, row in enumerate(sheet_rows, start=1):
        sheet_lines.append(f'<row r="{r_idx}">')
        for c_idx, val in enumerate(row):
            sheet_lines.append(_cell_xml(c_idx, r_idx, val))
        sheet_lines.append("</row>")
    sheet_lines.extend(["</sheetData>", "</worksheet>"])
    sheet_xml = "".join(sheet_lines)

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="红人列表" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


def _format_metric(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 10_000:
            return f"{value / 1_000:.1f}K"
        return str(round(value))
    if isinstance(value, int) and not isinstance(value, bool):
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 10_000:
            return f"{value / 1_000:.1f}K"
        return str(value)
    return str(value).strip()


def _audience_summary(facts: dict[str, Any]) -> str:
    parts: list[str] = []
    seen_labels: set[str] = set()
    for key, label in _AUDIENCE_SUMMARY_FIELDS:
        if label in seen_labels:
            continue
        raw = facts.get(key)
        if raw is None or raw == "":
            continue
        text = _format_metric(raw) if not isinstance(raw, str) else raw.strip()
        if not text:
            continue
        parts.append(f"{label}:{text}")
        seen_labels.add(label)
    return "；".join(parts)


def _enrich_items(
    conn: sqlite3.Connection,
    env: str,
    raw_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    campaign_ids = sorted({
        str(row.get("latest_campaign_id"))
        for row in raw_items
        if row.get("latest_campaign_id")
    })
    sku_by_campaign: dict[str, str] = {}
    if campaign_ids:
        placeholders = ",".join("?" * len(campaign_ids))
        rows = conn.execute(
            f"SELECT campaign_id, sku FROM product_campaigns "
            f"WHERE env=? AND campaign_id IN ({placeholders})",
            (env, *campaign_ids),
        ).fetchall()
        sku_by_campaign = {str(r["campaign_id"]): str(r["sku"]) for r in rows}

    out: list[dict[str, Any]] = []
    for row in raw_items:
        cid = row.get("latest_campaign_id")
        campaign_sku = sku_by_campaign.get(str(cid)) if cid else None
        target_spu = campaign_sku or row.get("target_spu")
        out.append({**row, "target_spu": target_spu})
    return out


async def fetch_all_registry_rows(
    bridge: BridgeClient,
    *,
    env: str,
    q: str | None = None,
    source: str = "all",
) -> list[dict[str, Any]]:
    """Page through bridge ``/kol-registry`` until all rows are loaded."""
    offset = 0
    items: list[dict[str, Any]] = []
    total = None
    while offset < _MAX_EXPORT_ROWS:
        page = await bridge.list_kol_registry(
            env=env, q=q, source=source, sort="ingested_at", order="desc",
            limit=_PAGE_SIZE, offset=offset,
        )
        if total is None:
            total = int(page.get("total") or 0)
        batch = page.get("items") or []
        if not isinstance(batch, list) or not batch:
            break
        for row in batch:
            if isinstance(row, dict):
                items.append(row)
        offset += len(batch)
        if offset >= total or len(batch) < _PAGE_SIZE:
            break
    return items[:_MAX_EXPORT_ROWS]


def registry_rows_for_sheet(items: list[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for idx, row in enumerate(items, start=1):
        handle = str(row.get("handle") or "").lstrip("@")
        facts = row.get("audience_facts") if isinstance(row.get("audience_facts"), dict) else {}
        rows.append([
            idx,
            handle or f"kol#{row.get('identity_id')}",
            row.get("ig_url") or "",
            int(row.get("internal_touch_count") or 0),
            row.get("target_spu") or "",
            _format_metric(row.get("followers")),
            _format_metric(row.get("avg_views")),
            _audience_summary(facts),
            row.get("email") or "",
            "是" if row.get("has_initial_outreach_draft") else "否",
            "是" if row.get("has_inbound_reply") else "否",
        ])
    return rows


def export_filename(env: str) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    return f"Agent红人列表_{env}_{stamp}.xlsx"


async def build_registry_xlsx(
    bridge: BridgeClient,
    conn: sqlite3.Connection,
    *,
    env: str,
    q: str | None = None,
    source: str = "all",
) -> tuple[bytes, str, int]:
    """Return (xlsx_bytes, filename, row_count)."""
    env_norm = env.upper()
    try:
        raw = await fetch_all_registry_rows(
            bridge, env=env_norm, q=q, source=source,
        )
    except BridgeError:
        raise
    items = _enrich_items(conn, env_norm, raw)
    data_rows = registry_rows_for_sheet(items)
    content = rows_to_xlsx_bytes(list(_EXPORT_HEADERS), data_rows)
    return content, export_filename(env_norm), len(data_rows)
