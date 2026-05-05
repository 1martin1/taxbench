import json
import os
import sqlite3
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Extra

app = FastAPI(title="Product Catalog", version="1.0.11")

DB_PATH = "db.sqlite3"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                name TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        conn.commit()


init_db()


class AddProductModel(BaseModel, extra=Extra.allow):
    name: str


class UploadModel(BaseModel):
    name: str
    data: str


def store_product(name: str, data_dict: Dict[str, Any]) -> None:
    data_json = json.dumps(data_dict, ensure_ascii=False)
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO products (name, data) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET data=excluded.data
            """,
            (name, data_json),
        )
        conn.commit()


def fetch_product(name: str) -> Dict[str, Any]:
    with get_db_connection() as conn:
        cur = conn.execute("SELECT data FROM products WHERE name = ?", (name,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return json.loads(row["data"])


def fetch_all_products() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cur = conn.execute("SELECT data FROM products")
        rows = cur.fetchall()
        return [json.loads(row["data"]) for row in rows]


@app.post("/add", status_code=status.HTTP_201_CREATED)
async def add_product(payload: AddProductModel):
    # payload already validated, contains at least 'name' and any extra fields
    product_dict = payload.dict()
    name = product_dict.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    store_product(name, product_dict)
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"detail": "Product added"})


@app.get("/download")
async def download_product(name: str = Query(..., description="The name of the product")):
    product = fetch_product(name)
    # Return the whole product dict as a JSON string in the 'data' field
    return {"data": json.dumps(product, ensure_ascii=False)}


@app.post("/upload")
async def upload_product(payload: UploadModel):
    try:
        product_data = json.loads(payload.data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in data field")
    if not isinstance(product_data, dict):
        raise HTTPException(status_code=400, detail="Data must be a JSON object")
    # Ensure the name in payload matches the name inside the data (if present)
    if "name" in product_data and product_data["name"] != payload.name:
        raise HTTPException(
            status_code=400,
            detail="Name mismatch between payload and data object",
        )
    # Force the correct name into the stored dict
    product_data["name"] = payload.name
    store_product(payload.name, product_data)
    return {"detail": "Product updated"}


@app.get("/search")
async def search_products(query: str = Query(..., description="Search query")):
    if not query:
        raise HTTPException(status_code=400, detail="Query parameter is required")
    query_lower = query.lower()
    matching: List[Dict[str, Any]] = []
    for product in fetch_all_products():
        # Check name
        if query_lower in str(product.get("name", "")).lower():
            matching.append(product)
            continue
        # Check any other string property
        for key, value in product.items():
            if key == "name":
                continue
            if isinstance(value, str) and query_lower in value.lower():
                matching.append(product)
                break
    return {"results": matching}
    

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)