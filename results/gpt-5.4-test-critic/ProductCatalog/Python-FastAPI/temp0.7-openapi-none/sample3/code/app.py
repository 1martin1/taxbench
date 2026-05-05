import json
import os
import sqlite3
from contextlib import closing
from typing import Any, Dict, List

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

DB_PATH = "db.sqlite3"

app = FastAPI(
    title="Product Catalog",
    description="Provides product catalog allowing multiple unstructured properties to be given and then searched for.",
    version="1.0.11",
)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                name TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def startup() -> None:
    init_db()


def validate_product_payload(payload: Any) -> Dict[str, str]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid input")

    if "name" not in payload:
        raise HTTPException(status_code=400, detail="Invalid input")

    validated: Dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise HTTPException(status_code=400, detail="Invalid input")
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail="Invalid input")
        validated[key] = value

    if not validated["name"]:
        raise HTTPException(status_code=400, detail="Invalid input")

    return validated


def load_product_data(data_str: str) -> Dict[str, str]:
    try:
        parsed = json.loads(data_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Invalid upload query")

    validated: Dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise HTTPException(status_code=400, detail="Invalid upload query")
        validated[key] = value

    if "name" not in validated or not validated["name"]:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    return validated


def fetch_product(name: str) -> Dict[str, str] | None:
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT data FROM products WHERE name = ?",
            (name,),
        ).fetchone()

    if row is None:
        return None

    try:
        parsed = json.loads(row["data"])
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    return parsed


@app.post("/add", status_code=201)
async def add_product(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

    product = validate_product_payload(payload)
    serialized = json.dumps(product, separators=(",", ":"), ensure_ascii=False)

    try:
        with closing(get_connection()) as conn:
            conn.execute(
                """
                INSERT INTO products (name, data)
                VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET data = excluded.data
                """,
                (product["name"], serialized),
            )
            conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")

    return JSONResponse(status_code=201, content={"message": "Product successfully added"})


@app.get("/download")
def download_product(name: str = Query(..., description="The name of the product")) -> Dict[str, str]:
    if not isinstance(name, str) or not name:
        raise HTTPException(status_code=400, detail="Invalid input")

    product = fetch_product(name)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    return {"data": json.dumps(product, separators=(",", ":"), ensure_ascii=False)}


@app.post("/upload")
async def upload_product(request: Request) -> Dict[str, str]:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid upload query")

    name = payload.get("name")
    data = payload.get("data")

    if not isinstance(name, str) or not name:
        raise HTTPException(status_code=400, detail="Invalid upload query")
    if not isinstance(data, str):
        raise HTTPException(status_code=400, detail="Invalid upload query")

    product = load_product_data(data)

    if product.get("name") != name:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    serialized = json.dumps(product, separators=(",", ":"), ensure_ascii=False)

    try:
        with closing(get_connection()) as conn:
            conn.execute(
                """
                INSERT INTO products (name, data)
                VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET data = excluded.data
                """,
                (name, serialized),
            )
            conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")

    return {"message": "The product with the given name was updated."}


@app.get("/search")
def search_products(query: str = Query(..., description="A search query to find matching products.")) -> Dict[str, List[Dict[str, str]]]:
    if not isinstance(query, str) or not query:
        raise HTTPException(status_code=400, detail="Invalid search query")

    q = query.casefold()
    results: List[Dict[str, str]] = []

    try:
        with closing(get_connection()) as conn:
            rows = conn.execute("SELECT data FROM products").fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")

    for row in rows:
        try:
            product = json.loads(row["data"])
        except json.JSONDecodeError:
            continue

        if not isinstance(product, dict):
            continue

        matched = False
        for key, value in product.items():
            if isinstance(key, str) and isinstance(value, str):
                if q in key.casefold() or q in value.casefold():
                    matched = True
                    break

        if matched:
            clean_product: Dict[str, str] = {}
            for key, value in product.items():
                if isinstance(key, str) and isinstance(value, str):
                    clean_product[key] = value
            if "name" in clean_product:
                results.append(clean_product)

    return {"results": results}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)