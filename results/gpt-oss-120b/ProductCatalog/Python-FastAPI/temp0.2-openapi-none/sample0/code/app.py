import json
import os
import sqlite3
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Extra

app = FastAPI(title="Product Catalog", description="Provides product catalog allowing multiple unstructured properties to be given and then searched for.", version="1.0.11")

DB_PATH = "db.sqlite3"

# Initialize SQLite connection
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

conn = get_connection()

def init_db() -> None:
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                name TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )

init_db()

# Pydantic model for adding a product (allows arbitrary extra fields)
class AddProductModel(BaseModel):
    name: str

    class Config:
        extra = Extra.allow  # accept any additional string fields

# Model for uploading a product (data is a JSON string)
class UploadModel(BaseModel):
    name: str
    data: str

def fetch_product(name: str) -> Dict[str, Any]:
    cur = conn.execute("SELECT data FROM products WHERE name = ?", (name,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Product not found")
    product_dict = json.loads(row["data"])
    return product_dict

@app.post("/add", status_code=status.HTTP_201_CREATED, summary="Add a new product to the catalog")
async def add_product(payload: AddProductModel):
    product_dict = payload.dict()
    name = product_dict["name"]
    # Ensure all extra fields are strings (as per schema)
    for key, value in product_dict.items():
        if key != "name" and not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"Extra field '{key}' must be a string")
    # Check existence
    cur = conn.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    if cur.fetchone():
        raise HTTPException(status_code=400, detail="Product with this name already exists")
    # Store as JSON
    data_json = json.dumps(product_dict)
    with conn:
        conn.execute("INSERT INTO products (name, data) VALUES (?, ?)", (name, data_json))
    return {"message": "Product added"}

@app.get("/download", summary="Download the entire current catalog with its unstructured properties for a given product name as a data entry.")
async def download_product(name: str = Query(..., description="The name of the product")):
    product = fetch_product(name)
    # Return the whole product dict as a JSON string in the 'data' field
    return {"data": json.dumps(product)}

@app.post("/upload", summary="Upload data for a product with the given name in the catalog. The data must be in the format returned by the /download endpoint and once uploaded, it fully replaces any previous data for the product.")
async def upload_product(payload: UploadModel):
    name = payload.name
    try:
        product_dict = json.loads(payload.data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in 'data' field")
    if not isinstance(product_dict, dict):
        raise HTTPException(status_code=400, detail="'data' must be a JSON object")
    # Ensure the name matches (or at least the stored name is consistent)
    product_dict["name"] = name  # enforce correct name
    # Validate that all values are strings (except name)
    for key, value in product_dict.items():
        if key != "name" and not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"Field '{key}' must be a string")
    data_json = json.dumps(product_dict)
    with conn:
        conn.execute(
            "INSERT INTO products (name, data) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET data=excluded.data",
            (name, data_json),
        )
    return {"message": "Product updated"}

@app.get("/search", summary="Search for products in the catalog")
async def search_products(query: str = Query(..., description="A search query to find matching products. Matches its name or any other property.")):
    if not query:
        raise HTTPException(status_code=400, detail="Query parameter cannot be empty")
    q_lower = query.lower()
    cur = conn.execute("SELECT data FROM products")
    results: List[Dict[str, Any]] = []
    for row in cur:
        product = json.loads(row["data"])
        # Check name
        if q_lower in product.get("name", "").lower():
            results.append(product)
            continue
        # Check other fields
        for key, value in product.items():
            if key == "name":
                continue
            if isinstance(value, str) and q_lower in value.lower():
                results.append(product)
                break
    return {"results": results}