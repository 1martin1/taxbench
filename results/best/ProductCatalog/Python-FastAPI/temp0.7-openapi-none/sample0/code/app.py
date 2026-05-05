import json
import os
import re
import sqlite3
from contextlib import closing
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Body, FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

DB_PATH = "db.sqlite3"

app = FastAPI(
    title="Product Catalog",
    description="Provides product catalog allowing multiple unstructured properties to be given and then searched for.",
    version="1.0.11",
)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    with closing(get_conn()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS product_properties (
                product_id INTEGER NOT NULL,
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                PRIMARY KEY (product_id, key),
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            )
            """
        )


def fetch_product_id(conn: sqlite3.Connection, name: str) -> Optional[int]:
    cur = conn.execute("SELECT id FROM products WHERE name = ?", (name,))
    row = cur.fetchone()
    return int(row["id"]) if row else None


def insert_product(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute("INSERT INTO products (name) VALUES (?)", (name,))
    return int(cur.lastrowid)


def get_properties(conn: sqlite3.Connection, product_id: int) -> Dict[str, str]:
    cur = conn.execute(
        "SELECT key, value FROM product_properties WHERE product_id = ? ORDER BY key ASC",
        (product_id,),
    )
    props: Dict[str, str] = {}
    for row in cur.fetchall():
        props[str(row["key"])] = str(row["value"])
    return props


def replace_properties(conn: sqlite3.Connection, product_id: int, props: Dict[str, str]) -> None:
    conn.execute("DELETE FROM product_properties WHERE product_id = ?", (product_id,))
    if props:
        conn.executemany(
            "INSERT INTO product_properties (product_id, key, value) VALUES (?, ?, ?)",
            [(product_id, k, v) for k, v in props.items()],
        )

def validate_product_name(name: str) -> str:
    if len(name) > 255:
        raise HTTPException(status_code=400, detail="Product name too long")
    name_escaped = re.escape(name)
    return name_escaped


def validate_product_payload(payload: Dict[str, Any]) -> Tuple[str, Dict[str, str]]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid input: body must be a JSON object")

    if "name" not in payload:
        raise HTTPException(status_code=400, detail="Invalid input: 'name' is required")

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="Invalid input: 'name' must be a non-empty string")
    name = validate_product_name(name.strip())

    props: Dict[str, str] = {}
    for k, v in payload.items():
        if k == "name":
            continue
        if not isinstance(k, str):
            raise HTTPException(status_code=400, detail="Invalid input: property keys must be strings")
        if not isinstance(v, str):
            raise HTTPException(status_code=400, detail=f"Invalid input: value for '{k}' must be a string")
        if not k:
            raise HTTPException(status_code=400, detail="Invalid input: property key cannot be empty")
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_\-]*$', k):
            raise HTTPException(status_code=400, detail=f"Invalid property key format: '{k}'")

        props[k] = v
    return name, props


def parse_upload_data(data_str: str) -> Dict[str, str]:
    try:
        obj = json.loads(data_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid upload query: 'data' must be a JSON string of an object")
    if not isinstance(obj, dict):
        raise HTTPException(status_code=400, detail="Invalid upload query: 'data' must be a JSON object")

    props: Dict[str, str] = {}
    for k, v in obj.items():
        if not isinstance(k, str):
            raise HTTPException(status_code=400, detail="Invalid upload query: all property keys must be strings")
        if k == "name":
            continue
        if not isinstance(v, str):
            raise HTTPException(status_code=400, detail=f"Invalid upload query: value for '{k}' must be a string")
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_\-]*$', k):
            raise HTTPException(status_code=400, detail=f"Invalid property key format: '{k}'")

        props[k] = v
    return props


class UploadBody(BaseModel):
    name: str
    data: str


class DownloadResponse(BaseModel):
    data: str


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.post("/add", status_code=status.HTTP_201_CREATED)
def add_product(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    name, props = validate_product_payload(payload)
    with closing(get_conn()) as conn, conn:
        existing_id = fetch_product_id(conn, name)
        if existing_id is not None:
            raise HTTPException(status_code=400, detail="Invalid input: product with this name already exists")
        product_id = insert_product(conn, name)
        replace_properties(conn, product_id, props)
    return JSONResponse(
        status_code=201,
        content={"message": "Product successfully added"},
    )


@app.get("/download", response_model=DownloadResponse)
def download_product(name: str = Query(..., description="The name of the product")) -> DownloadResponse:
    name = validate_product_name(name)
    with closing(get_conn()) as conn:
        product_id = fetch_product_id(conn, name)
        if product_id is None:
            data_str = json.dumps({}, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
            return DownloadResponse(data=data_str)
        props = get_properties(conn, product_id)
        data_str = json.dumps(props, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
        return DownloadResponse(data=data_str)


@app.post("/upload")
def upload_product(body: UploadBody) -> JSONResponse:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Invalid upload query: 'name' must be a non-empty string")
    name = validate_product_name(name)
    props = parse_upload_data(body.data)

    with closing(get_conn()) as conn, conn:
        product_id = fetch_product_id(conn, name)
        if product_id is None:
            try:
                product_id = insert_product(conn, name)
            except sqlite3.IntegrityError:
                product_id = fetch_product_id(conn, name)
                if product_id is None:
                    raise HTTPException(status_code=409, detail="Conflict creating product")
        replace_properties(conn, product_id, props)

    return JSONResponse(
        status_code=200,
        content={"message": "The product with the given name was updated."},
    )


@app.get("/search")
def search_products(
        query: str = Query(...,
                           description="A search query to find matching products. Matches its name or any other property.")
) -> Dict[str, List[Dict[str, str]]]:
    q = query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Invalid search query: 'query' must be a non-empty string")
    q_escaped = re.escape(q.lower())
    like = f"%{q_escaped}%"

    with closing(get_conn()) as conn:
        cur = conn.execute(
            """
            SELECT DISTINCT p.id
            FROM products p
            LEFT JOIN product_properties pr ON pr.product_id = p.id
            WHERE LOWER(p.name) LIKE ?
               OR LOWER(pr.key) LIKE ?
               OR LOWER(pr.value) LIKE ?
            """,
            (like, like, like),
        )
        ids = [int(row["id"]) for row in cur.fetchall()]
        results: List[Dict[str, str]] = []
        if ids:
            placeholders = ",".join("?" for _ in ids)
            cur_names = conn.execute(
                f"SELECT id, name FROM products WHERE id IN ({placeholders}) ORDER BY name ASC",
                ids,
            )
            id_to_name = {int(r["id"]): str(r["name"]) for r in cur_names.fetchall()}
            cur_props = conn.execute(
                f"""
                SELECT product_id, key, value
                FROM product_properties
                WHERE product_id IN ({placeholders})
                ORDER BY product_id, key
                """,
                ids,
            )
            props_map: Dict[int, Dict[str, str]] = {pid: {} for pid in ids}
            for row in cur_props.fetchall():
                pid = int(row["product_id"])
                props_map.setdefault(pid, {})
                props_map[pid][str(row["key"])] = str(row["value"])

            for pid in ids:
                item: Dict[str, str] = {"name": id_to_name.get(pid, "")}
                item.update(props_map.get(pid, {}))
                results.append(item)

    return {"results": results}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)