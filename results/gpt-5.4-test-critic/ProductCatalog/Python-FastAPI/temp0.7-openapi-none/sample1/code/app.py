import json
import os
import sqlite3
from contextlib import contextmanager
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
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


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


def serialize_product(product: Dict[str, str]) -> str:
    return json.dumps(product, ensure_ascii=False, separators=(",", ":"))


def deserialize_product(data: str) -> Dict[str, str]:
    try:
        product = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    if not isinstance(product, dict):
        raise HTTPException(status_code=400, detail="Invalid upload query")

    validated: Dict[str, str] = {}
    for key, value in product.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise HTTPException(status_code=400, detail="Invalid upload query")
        validated[key] = value

    if "name" not in validated or not validated["name"]:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    return validated


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.post("/add", status_code=201)
async def add_product(request: Request):
    payload = await request.json()
    product = validate_product_payload(payload)

    with get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO products (name, data)
            VALUES (?, ?)
            """,
            (product["name"], serialize_product(product)),
        )

    return JSONResponse(status_code=201, content={"message": "Product successfully added"})


@app.get("/download")
def download_product(name: str = Query(..., description="The name of the product")):
    with get_db() as conn:
        row = conn.execute(
            "SELECT data FROM products WHERE name = ?",
            (name,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Product not found")

    return {"data": row[0]}


@app.post("/upload")
async def upload_product(request: Request):
    payload = await request.json()

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid upload query")

    name = payload.get("name")
    data = payload.get("data")

    if not isinstance(name, str) or not name:
        raise HTTPException(status_code=400, detail="Invalid upload query")
    if not isinstance(data, str):
        raise HTTPException(status_code=400, detail="Invalid upload query")

    product = deserialize_product(data)

    if product.get("name") != name:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    with get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO products (name, data)
            VALUES (?, ?)
            """,
            (name, data),
        )

    return {"message": "The product with the given name was updated."}


@app.get("/search")
def search_products(query: str = Query(..., description="A search query to find matching products.")):
    if not isinstance(query, str) or not query:
        raise HTTPException(status_code=400, detail="Invalid search query")

    like_query = f"%{query.lower()}%"
    results: List[Dict[str, str]] = []

    with get_db() as conn:
        rows = conn.execute("SELECT data FROM products").fetchall()

    for (data,) in rows:
        try:
            product = json.loads(data)
        except json.JSONDecodeError:
            continue

        if not isinstance(product, dict):
            continue

        matched = False
        for key, value in product.items():
            if isinstance(key, str) and isinstance(value, str):
                if like_query.strip("%") in value.lower():
                    matched = True
                    break

        if matched and "name" in product and isinstance(product["name"], str):
            results.append({k: v for k, v in product.items() if isinstance(k, str) and isinstance(v, str)})

    return {"results": results}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)