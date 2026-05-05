import os
import json
import sqlite3
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query, Body, Depends, status
from pydantic import BaseModel, Field, Extra

# ---------- Database utilities ----------
DB_PATH = "db.sqlite3"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
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

# Initialize DB at import time
init_db()

# ---------- Pydantic models ----------
class ProductModel(BaseModel):
    name: str = Field(..., description="The name of the product")

    class Config:
        extra = Extra.allow  # allow arbitrary additional string fields

class UploadModel(BaseModel):
    name: str = Field(..., description="The name of the product")
    data: str = Field(..., description="The data of a product (JSON string)")

# ---------- FastAPI app ----------
app = FastAPI(title="Product Catalog", version="1.0.11")

# ---------- Endpoints ----------
@app.post("/add", status_code=status.HTTP_201_CREATED)
def add_product(product: ProductModel = Body(...)):
    """
    Add a new product with arbitrary string properties.
    """
    product_dict: Dict[str, Any] = product.model_dump()
    # Ensure all additional properties are strings (as per OpenAPI spec)
    for key, value in product_dict.items():
        if not isinstance(value, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"All property values must be strings. Property '{key}' is not."
            )
    name = product_dict["name"]
    data_json = json.dumps(product_dict)

    conn = get_connection()
    try:
        cur = conn.execute("SELECT 1 FROM products WHERE name = ?", (name,))
        if cur.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product with name '{name}' already exists."
            )
        conn.execute(
            "INSERT INTO products (name, data) VALUES (?, ?)",
            (name, data_json)
        )
        conn.commit()
    finally:
        conn.close()
    return {"message": "Product added successfully."}


@app.get("/download")
def download_product(name: str = Query(..., description="The name of the product")):
    """
    Download the entire current catalog entry for a given product name.
    """
    conn = get_connection()
    try:
        cur = conn.execute("SELECT data FROM products WHERE name = ?", (name,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product with name '{name}' not found."
            )
        data = row["data"]
    finally:
        conn.close()
    return {"data": data}


@app.post("/upload")
def upload_product(upload: UploadModel = Body(...)):
    """
    Upload data for a product; replaces any existing data for that product.
    """
    # Validate that `data` is a proper JSON string and contains a `name` field matching the provided name
    try:
        data_dict = json.loads(upload.data)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provided data is not valid JSON."
        )
    if not isinstance(data_dict, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provided data JSON must be an object."
        )
    # Optional consistency check: ensure the name inside data matches the path name
    inner_name = data_dict.get("name")
    if inner_name and inner_name != upload.name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name inside data does not match the provided name."
        )
    # Ensure all values are strings (as per schema)
    for k, v in data_dict.items():
        if not isinstance(v, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"All property values must be strings. Property '{k}' is not."
            )
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO products (name, data) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET data=excluded.data
            """,
            (upload.name, upload.data)
        )
        conn.commit()
    finally:
        conn.close()
    return {"message": f"Product '{upload.name}' updated successfully."}


@app.get("/search")
def search_products(query: str = Query(..., description="Search query matching name or any property")):
    """
    Search for products whose name or any property contains the query string (case‑insensitive).
    """
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter cannot be empty."
        )
    lowered = query.lower()
    conn = get_connection()
    results: List[Dict[str, Any]] = []
    try:
        cur = conn.execute("SELECT data FROM products")
        rows = cur.fetchall()
        for row in rows:
            data_json = row["data"]
            try:
                product_dict = json.loads(data_json)
            except json.JSONDecodeError:
                continue  # skip malformed entries
            # Check name and all other string fields
            match = False
            for value in product_dict.values():
                if isinstance(value, str) and lowered in value.lower():
                    match = True
                    break
            if match:
                results.append(product_dict)
    finally:
        conn.close()
    return {"results": results}


# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=5000,
        log_level="info",
    )