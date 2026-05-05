import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Generator, List

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response

DB_PATH = "db.sqlite3"

MAX_FIELDS = 128
MAX_FIELD_NAME_LENGTH = 128
MAX_FIELD_VALUE_LENGTH = 4096
MAX_PRODUCT_JSON_SIZE = 65536
MAX_SEARCH_QUERY_LENGTH = 256

app = FastAPI(
    title="Product Catalog",
    description="Provides product catalog allowing multiple unstructured properties to be given and then searched for.",
    version="1.0.11",
)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                name TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_name ON products(name)")
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def validate_product_dict(
    payload: Any,
    error_detail: str,
    *,
    enforce_serialized_size: bool = True,
) -> Dict[str, str]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail=error_detail)

    if "name" not in payload:
        raise HTTPException(status_code=400, detail=error_detail)

    if len(payload) > MAX_FIELDS:
        raise HTTPException(status_code=400, detail=error_detail)

    validated: Dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise HTTPException(status_code=400, detail=error_detail)
        if not key or len(key) > MAX_FIELD_NAME_LENGTH:
            raise HTTPException(status_code=400, detail=error_detail)
        if len(value) > MAX_FIELD_VALUE_LENGTH:
            raise HTTPException(status_code=400, detail=error_detail)
        validated[key] = value

    if not validated["name"] or len(validated["name"]) > MAX_FIELD_VALUE_LENGTH:
        raise HTTPException(status_code=400, detail=error_detail)

    if enforce_serialized_size:
        serialized = json.dumps(validated, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > MAX_PRODUCT_JSON_SIZE:
            raise HTTPException(status_code=400, detail=error_detail)

    return validated


def serialize_product(product: Dict[str, str]) -> str:
    return json.dumps(product, ensure_ascii=False, separators=(",", ":"))


def deserialize_product(data: str, error_detail: str) -> Dict[str, str]:
    if not isinstance(data, str):
        raise HTTPException(status_code=400, detail=error_detail)

    if len(data.encode("utf-8")) > MAX_PRODUCT_JSON_SIZE:
        raise HTTPException(status_code=400, detail=error_detail)

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=error_detail)

    return validate_product_dict(parsed, error_detail, enforce_serialized_size=True)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/add", status_code=201)
async def add_product(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

    product = validate_product_dict(payload, "Invalid input", enforce_serialized_size=True)
    name = product["name"]
    data = serialize_product(product)

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO products (name, data) VALUES (?, ?)",
                (name, data),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid input")
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Invalid input")

    return Response(status_code=201)


@app.get("/download")
def download_product(name: str = Query(..., description="The name of the product")) -> Dict[str, str]:
    if not isinstance(name, str) or not name or len(name) > MAX_FIELD_VALUE_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT data FROM products WHERE name = ?",
                (name,),
            ).fetchone()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Invalid input")

    if row is None:
        raise HTTPException(status_code=400, detail="Invalid input")

    data = row["data"]
    if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_PRODUCT_JSON_SIZE:
        raise HTTPException(status_code=400, detail="Invalid input")

    return {"data": data}


@app.post("/upload")
async def upload_product(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid upload query")

    name = payload.get("name")
    data = payload.get("data")

    if not isinstance(name, str) or not name or len(name) > MAX_FIELD_VALUE_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid upload query")
    if not isinstance(data, str):
        raise HTTPException(status_code=400, detail="Invalid upload query")
    if len(data.encode("utf-8")) > MAX_PRODUCT_JSON_SIZE:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    product = deserialize_product(data, "Invalid upload query")

    if product.get("name") != name:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    normalized_data = serialize_product(product)

    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO products (name, data)
                VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET data = excluded.data
                """,
                (name, normalized_data),
            )
            conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    return Response(status_code=200)


@app.get("/search")
def search_products(
    query: str = Query(
        ...,
        description="A search query to find matching products. Matches its name or any other property.",
    )
) -> Dict[str, List[Dict[str, str]]]:
    if not isinstance(query, str) or not query or len(query) > MAX_SEARCH_QUERY_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid search query")

    lowered_query = query.lower()
    results: List[Dict[str, str]] = []

    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT data
                FROM products
                WHERE lower(name) LIKE ?
                """,
                (f"%{lowered_query}%",),
            ).fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Invalid search query")

    for row in rows:
        product = deserialize_product(row["data"], "Invalid search query")
        results.append(product)

    if len(results) < 1000:
        try:
            with get_db() as conn:
                rows = conn.execute(
                    """
                    SELECT data
                    FROM products
                    WHERE lower(name) NOT LIKE ?
                      AND lower(data) LIKE ?
                    """,
                    (f"%{lowered_query}%", f"%{lowered_query}%"),
                ).fetchall()
        except sqlite3.Error:
            raise HTTPException(status_code=400, detail="Invalid search query")

        for row in rows:
            product = deserialize_product(row["data"], "Invalid search query")
            matched = False
            for value in product.values():
                if lowered_query in value.lower():
                    matched = True
                    break
            if matched:
                results.append(product)

    return {"results": results}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)