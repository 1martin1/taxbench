import json
import sqlite3
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query, Request, status
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


@app.on_event("startup")
def startup_event():
    init_db()


class ProductModel(BaseModel):
    name: str

    class Config:
        extra = Extra.allow  # allow arbitrary additional string fields


class UploadModel(BaseModel):
    name: str
    data: str  # JSON string representing the product


def _store_product(product_dict: Dict[str, Any]) -> None:
    name = product_dict["name"]
    data_json = json.dumps(product_dict, ensure_ascii=False)
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO products (name, data) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET data=excluded.data
            """,
            (name, data_json),
        )
        conn.commit()


def _load_product(name: str) -> Dict[str, Any]:
    with get_db_connection() as conn:
        cur = conn.execute("SELECT data FROM products WHERE name = ?", (name,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return json.loads(row["data"])


def _load_all_products() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cur = conn.execute("SELECT data FROM products")
        rows = cur.fetchall()
        return [json.loads(row["data"]) for row in rows]


@app.post("/add", status_code=status.HTTP_201_CREATED)
def add_product(product: ProductModel):
    product_dict = product.dict()
    if not product_dict.get("name"):
        raise HTTPException(status_code=400, detail="Name field is required")
    # Ensure all additional fields are strings
    for key, value in product_dict.items():
        if key != "name" and not isinstance(value, str):
            raise HTTPException(
                status_code=400,
                detail=f"Additional field '{key}' must be a string",
            )
    _store_product(product_dict)
    return {"message": "Product added successfully"}


@app.get("/download")
def download_product(name: str = Query(..., description="The name of the product")):
    product = _load_product(name)
    # Return the whole product as a JSON string under the key "data"
    return {"data": json.dumps(product, ensure_ascii=False)}


@app.post("/upload")
def upload_product(upload: UploadModel):
    # Parse the data field which should be a JSON string
    try:
        product_dict = json.loads(upload.data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in data field")
    if not isinstance(product_dict, dict):
        raise HTTPException(status_code=400, detail="Data must be a JSON object")
    # Ensure the name matches
    if product_dict.get("name") != upload.name:
        raise HTTPException(
            status_code=400,
            detail="Name in payload does not match name field",
        )
    # Validate that all values are strings (as per schema)
    for key, value in product_dict.items():
        if not isinstance(value, str):
            raise HTTPException(
                status_code=400,
                detail=f"Field '{key}' must be a string",
            )
    _store_product(product_dict)
    return {"message": "Product updated successfully"}


@app.get("/search")
def search_products(query: str = Query(..., description="Search query")):
    if not query:
        raise HTTPException(status_code=400, detail="Query parameter cannot be empty")
    all_products = _load_all_products()
    lowered = query.lower()
    results = []
    for prod in all_products:
        # Check name
        if lowered in prod.get("name", "").lower():
            results.append(prod)
            continue
        # Check any other property values
        for key, value in prod.items():
            if key == "name":
                continue
            if isinstance(value, str) and lowered in value.lower():
                results.append(prod)
                break
    return {"results": results}