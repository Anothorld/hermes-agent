"""Product (SKU) catalog stored locally."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..deps import current_user, get_conn, require_role

router = APIRouter(prefix="/products", tags=["products"])


class ProductBody(BaseModel):
    sku: str = Field(min_length=1)
    name: str
    url: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None


@router.get("")
def list_products(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
) -> list[dict]:
    rows = conn.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d.pop("tags_json") or "[]")
        out.append(d)
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
def upsert_product(
    body: ProductBody,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO products (sku, name, url, tags_json, notes, created_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(sku) DO UPDATE SET
             name=excluded.name, url=excluded.url,
             tags_json=excluded.tags_json, notes=excluded.notes""",
        (body.sku, body.name, body.url, json.dumps(body.tags), body.notes, now),
    )
    write_audit(conn, actor_user_id=user["id"], action="product.upsert", target=body.sku)
    return {"ok": True, "sku": body.sku}


@router.get("/{sku}")
def get_product(
    sku: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
) -> dict:
    row = conn.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sku not found")
    d = dict(row)
    d["tags"] = json.loads(d.pop("tags_json") or "[]")
    return d
