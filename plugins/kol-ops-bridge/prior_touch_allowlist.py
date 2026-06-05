"""Legacy 曾触达列表 workbook index for internal touch counts.

``internal_touch_count`` = number of spreadsheet **rows** (across all sheets)
whose identifiers match the KOL handle or email (+1 per matching row).
"""

from __future__ import annotations

import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Iterable

_XLSX_NS: Final[dict[str, str]] = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}
_REL_NS: Final[str] = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_DEFAULT_JSON: Final[Path] = (
    Path(__file__).resolve().parent / "data" / "prior_touch_allowlist.json"
)
_DEFAULT_XLSX: Final[Path] = Path.home() / "Documents" / "曾触达列表.xlsx"
_LINK_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"instagram\.com/([^/?#]+)", re.I),
    re.compile(r"youtube\.com/(?:channel/|@)([^/?#]+)", re.I),
    re.compile(r"tiktok\.com/@([^/?#]+)", re.I),
)
_HEADER_SKIP: Final[frozenset[str]] = frozenset({
    "id", "网红id", "红人id", "序号", "日期", "平台", "粉丝", "邮箱", "链接",
    "国家/城市", "数值", "量级", "平均播放", "类型", "触达日期", "粉丝量",
})


def _normalize_handle(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lstrip("@").lower()
    if not text or text in ("id", "网红id", "红人id"):
        return None
    if text.isdigit():
        return None
    return text


def _normalize_email(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if "@" not in text:
        return None
    return text


def _id_key_handle(handle: str) -> str:
    return f"handle:{handle}"


def _id_key_email(email: str) -> str:
    return f"email:{email}"


@dataclass
class TouchIndex:
    """Row-level touch counts keyed by normalized handle/email identifiers."""

    row_keys_by_identifier: dict[str, set[str]] = field(default_factory=dict)

    @property
    def handles(self) -> set[str]:
        out: set[str] = set()
        for key in self.row_keys_by_identifier:
            if key.startswith("handle:"):
                out.add(key[7:])
        return out

    @property
    def emails(self) -> set[str]:
        out: set[str] = set()
        for key in self.row_keys_by_identifier:
            if key.startswith("email:"):
                out.add(key[6:])
        return out

    def lookup_count(self, *, handle: Any = None, email: Any = None) -> int:
        """Distinct matching rows across all sheets (+1 per row)."""
        keys: list[str] = []
        norm_handle = _normalize_handle(handle)
        if norm_handle:
            keys.append(_id_key_handle(norm_handle))
        norm_email = _normalize_email(email)
        if norm_email:
            keys.append(_id_key_email(norm_email))
        if not keys:
            return 0
        matched: set[str] = set()
        for key in keys:
            matched |= self.row_keys_by_identifier.get(key, set())
        return len(matched)

    def is_listed(self, *, handle: Any = None, email: Any = None) -> bool:
        return self.lookup_count(handle=handle, email=email) > 0

    def to_json_dict(self, *, source_file: str) -> dict[str, Any]:
        return {
            "source_file": source_file,
            "row_keys_by_identifier": {
                key: sorted(rows)
                for key, rows in sorted(self.row_keys_by_identifier.items())
            },
            "handles": sorted(self.handles),
            "emails": sorted(self.emails),
            "handle_count": len(self.handles),
            "email_count": len(self.emails),
        }

    @classmethod
    def from_json_dict(cls, payload: dict[str, Any]) -> TouchIndex:
        raw = payload.get("row_keys_by_identifier") or {}
        index = cls()
        if isinstance(raw, dict):
            for key, rows in raw.items():
                if not isinstance(key, str) or not isinstance(rows, list):
                    continue
                index.row_keys_by_identifier[key] = {
                    str(r) for r in rows if str(r).strip()
                }
        if index.row_keys_by_identifier:
            return index
        # Legacy bundle: one pseudo-row per listed handle/email.
        for handle in payload.get("handles") or []:
            norm = _normalize_handle(handle)
            if norm:
                index.row_keys_by_identifier.setdefault(
                    _id_key_handle(norm), set(),
                ).add(f"legacy!{norm}")
        for email in payload.get("emails") or []:
            norm = _normalize_email(email)
            if norm:
                index.row_keys_by_identifier.setdefault(
                    _id_key_email(norm), set(),
                ).add(f"legacy!{norm}")
        return index


def _cell_value(cell: ET.Element, strings: list[str]) -> str:
    val_el = cell.find("m:v", _XLSX_NS)
    if val_el is not None and val_el.text is not None:
        val = val_el.text
        if cell.get("t") == "s":
            return strings[int(val)]
        return str(val)
    is_el = cell.find("m:is", _XLSX_NS)
    if is_el is not None:
        parts = [t.text or "" for t in is_el.findall(".//m:t", _XLSX_NS)]
        return "".join(parts)
    return ""


def _parse_sheet_rows(
    sheet_xml: bytes,
    strings: list[str],
) -> dict[int, dict[str, str]]:
    root = ET.fromstring(sheet_xml)
    rows: dict[int, dict[str, str]] = {}
    for row in root.findall("m:sheetData/m:row", _XLSX_NS):
        rn = int(row.get("r", "0"))
        cells: dict[str, str] = {}
        for cell in row.findall("m:c", _XLSX_NS):
            ref = cell.get("r", "")
            col_match = re.match(r"([A-Z]+)", ref)
            if not col_match:
                continue
            text = _cell_value(cell, strings).strip()
            if text:
                cells[col_match.group(1)] = text
        if cells:
            rows[rn] = cells
    return rows


def _extract_identifiers_from_value(value: str) -> set[str]:
    out: set[str] = set()
    s = str(value).strip()
    if not s:
        return out
    email = _normalize_email(s)
    if email:
        out.add(_id_key_email(email))
    for pat in _LINK_PATTERNS:
        match = pat.search(s)
        if match:
            handle = _normalize_handle(match.group(1))
            if handle:
                out.add(_id_key_handle(handle))
    if "http" not in s.lower() and "@" not in s:
        handle = _normalize_handle(s)
        if handle and handle.lower() not in _HEADER_SKIP:
            out.add(_id_key_handle(handle))
    return out


def _extract_row_identifiers(cells: dict[str, str]) -> set[str]:
    """Collect handle/email identifiers from one spreadsheet row."""
    ids: set[str] = set()
    for value in cells.values():
        ids |= _extract_identifiers_from_value(value)
    return ids


def _workbook_sheet_paths(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_to = {r.get("Id"): r.get("Target") for r in rels}
    sheets: list[tuple[str, str]] = []
    for sheet in wb.findall("m:sheets/m:sheet", _XLSX_NS):
        name = str(sheet.get("name") or "sheet")
        rid = sheet.get(f"{{{_REL_NS}}}id")
        target = rid_to.get(rid or "", "")
        if not target:
            continue
        path = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
        sheets.append((name, path))
    return sheets


def parse_prior_touch_workbook(path: Path) -> TouchIndex:
    """Parse all sheets; each matching data row contributes +1 touch."""
    index = TouchIndex()
    with zipfile.ZipFile(path) as zf:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in ss_root.findall("m:si", _XLSX_NS):
                strings.append(
                    "".join((t.text or "") for t in si.findall(".//m:t", _XLSX_NS)),
                )
        for sheet_name, sheet_path in _workbook_sheet_paths(zf):
            if sheet_path not in zf.namelist():
                continue
            rows = _parse_sheet_rows(zf.read(sheet_path), strings)
            for rn, cells in rows.items():
                if rn < 2:
                    continue
                ids = _extract_row_identifiers(cells)
                if not ids:
                    continue
                row_key = f"{sheet_name}!{rn}"
                for ident in ids:
                    index.row_keys_by_identifier.setdefault(ident, set()).add(row_key)
    return index


def parse_prior_touch_xlsx(path: Path) -> tuple[set[str], set[str]]:
    """Backward-compatible handle/email sets (listed at least once)."""
    index = parse_prior_touch_workbook(path)
    return index.handles, index.emails


def resolve_allowlist_path() -> Path:
    """Resolve workbook/json source (env override → live xlsx → bundled JSON)."""
    json_path = os.environ.get("KOL_PRIOR_TOUCH_ALLOWLIST_JSON", "").strip()
    if json_path:
        return Path(json_path).expanduser()
    xlsx_path = os.environ.get("KOL_PRIOR_TOUCH_ALLOWLIST_XLSX", "").strip()
    if xlsx_path:
        return Path(xlsx_path).expanduser()
    if _DEFAULT_XLSX.exists():
        return _DEFAULT_XLSX
    return _DEFAULT_JSON


@lru_cache(maxsize=4)
def _cached_touch_index(path: str, mtime_ns: int) -> TouchIndex:
    file_path = Path(path)
    if file_path.suffix.lower() == ".xlsx":
        return parse_prior_touch_workbook(file_path)
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    return TouchIndex.from_json_dict(payload)


def get_touch_index() -> TouchIndex:
    """Load cached touch index from configured source."""
    path = resolve_allowlist_path()
    if not path.exists():
        return TouchIndex()
    stat = path.stat()
    return _cached_touch_index(str(path.resolve()), stat.st_mtime_ns)


def get_allowlist_sets() -> tuple[frozenset[str], frozenset[str]]:
    index = get_touch_index()
    return frozenset(index.handles), frozenset(index.emails)


def get_internal_touch_count(*, handle: Any = None, email: Any = None) -> int:
    """Spreadsheet row matches across all 曾触达列表 sheets."""
    return get_touch_index().lookup_count(handle=handle, email=email)


def is_prior_touch_allowlisted(*, handle: Any = None, email: Any = None) -> bool:
    """True when the KOL appears on the legacy workbook at least once."""
    return get_touch_index().is_listed(handle=handle, email=email)


def gate_internal_touch_count(
    raw_count: int,
    *,
    handle: Any = None,
    email: Any = None,
) -> int:
    """Deprecated alias — touch counts come from the workbook only."""
    del raw_count
    return get_internal_touch_count(handle=handle, email=email)


def write_allowlist_json(
    *,
    xlsx_path: Path,
    out_path: Path | None = None,
) -> Path:
    """Build ``prior_touch_allowlist.json`` from all workbook sheets."""
    index = parse_prior_touch_workbook(xlsx_path)
    target = out_path or _DEFAULT_JSON
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = index.to_json_dict(source_file=str(xlsx_path.resolve()))
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _cached_touch_index.cache_clear()
    return target


def clear_touch_index_cache() -> None:
    _cached_touch_index.cache_clear()
