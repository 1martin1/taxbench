import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Extra, Field, validator

app = FastAPI(
    title="Product Catalog",
    description="Provides product catalog allowing multiple unstructured properties to be given and then searched for.",
    version="1.0.11",
)

DB_PATH = "db.sqlite3"
MAX_EXTRA_FIELDS = 100          # safeguard against excessively large payloads
MAX_STRING_LENGTH = 10_000      # safeguard against very long strings
MAX_UPLOAD_DATA_SIZE = 50_000   # 50KB limit for the uploaded JSON string


@contextmanager
def get_db():
    """
    Dependency that provides a fresh SQLite connection per request.
    The connection is closed automatically after the request is processed.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """
    Initialise the SQLite database. This runs once at application startup.
    """
    with sqlite3.connect(DB_PATH) as conn:
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
def on_startup():
    init_db()


class AddProductModel(BaseModel, extra=Extra.allow):
    """
    Model for adding a product. Allows arbitrary extra string fields.
    """
    name: str = Field(..., description="The name of the product")

    @validator("*", pre=True, each_item=False)
    def ensure_string_fields(cls, v, field):
        # The 'name' field is already a string by type hint; other fields must be strings.
        if field.name != "name" and not isinstance(v, str):
            raise ValueError(f"Extra field '{field.name}' must be a string")
        if isinstance(v, str) and len(v) > MAX_STRING_LENGTH:
            raise ValueError(f"Field '{field.name}' exceeds maximum length")
        return v

    @validator("__root__", always=True)
    def limit_extra_fields(cls, values):
        # Pydantic does not expose extra fields directly; we count after model creation.
        # This validator runs after the model is built.
        extra_count = len(values) - 1  # subtract the mandatory 'name'
        if extra_count > MAX_EXTRA_FIELDS:
            raise ValueError(f"Too many extra fields (max {MAX_EXTRA_FIELDS})")
        return values


class UploadModel(BaseModel):
    """
    Model for uploading a product's data. The data field must contain a JSON string
    representing the full product object (including the name).
    """
    name: str = Field(..., description="The name of the product")
    data: str = Field(..., description="The data of a product (JSON string)")

    @validator("data")
    def limit_data_size(cls, v):
        if len(v.encode("utf-8")) > MAX_UPLOAD_DATA_SIZE:
            raise ValueError("Uploaded data exceeds size limit")
        return v


def fetch_product(conn: sqlite3.Connection, name: str) -> Dict[str, Any]:
    cur = conn.execute("SELECT data FROM products WHERE name = ?", (name,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Product not found")
    try:
        product_dict = json.loads(row["data"])
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Corrupted product data")
    return product_dict


@app.post(
    "/add",
    status_code=status.HTTP_201_CREATED,
    summary="Add a new product to the catalog",
)
async def add_product(
    payload: AddProductModel,
    db: sqlite3.Connection = Depends(get_db),
):
    product_dict = payload.dict()
    name = product_dict["name"]

    # Ensure the product does not already exist
    cur = db.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    if cur.fetchone():
        raise HTTPException(status_code=400, detail="Product with this name already exists")

    # Store the product as JSON
    data_json = json.dumps(product_dict, ensure_ascii=False)
    try:
        with db:
            db.execute(
                "INSERT INTO products (name, data) VALUES (?, ?)",
                (name, data_json),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Failed to add product due to integrity error")

    return {"message": "Product added"}


@app.get(
    "/download",
    summary="Download the entire current catalog with its unstructured properties for a given product name as a data entry.",
)
async def download_product(
    name: str = Query(..., description="The name of the product"),
    db: sqlite3.Connection = Depends(get_db),
):
    product = fetch_product(db, name)
    # Return the whole product dict as a JSON string in the 'data' field
    return {"data": json.dumps(product, ensure_ascii=False)}


@app.post(
    "/upload",
    summary="Upload data for a product with the given name in the catalog. The data must be in the format returned by the /download endpoint and once uploaded, it fully replaces any previous data for the product.",
)
async def upload_product(
    payload: UploadModel,
    db: sqlite3.Connection = Depends(get_db),
):
    name = payload.name

    # Parse the JSON string supplied by the client
    try:
        product_dict = json.loads(payload.data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in 'data' field")

    if not isinstance(product_dict, dict):
        raise HTTPException(status_code=400, detail="'data' must be a JSON object")

    # Enforce that the name inside the payload matches the URL/name field
    product_dict["name"] = name

    # Validate that all extra fields are strings and respect length limits
    for key, value in product_dict.items():
        if key != "name":
            if not isinstance(value, str):
                raise HTTPException(
                    status_code=400,
                    detail=f"Field '{key}' must be a string",
                )
            if len(value) > MAX_STRING_LENGTH:
                raise HTTPException(
                    status_code=400,
                    detail=f"Field '{key}' exceeds maximum length",
                )

    data_json = json.dumps(product_dict, ensure_ascii=False)

    # Use INSERT OR REPLACE for compatibility with older SQLite versions
    try:
        with db:
            db.execute(
                "INSERT OR REPLACE INTO products (name, data) VALUES (?, ?)",
                (name, data_json),
            )
    except sqlite3.DatabaseError as exc:
        raise HTTPException(status_code=500, detail="Database error during upload") from exc

    return {"message": "Product updated"}


@app.get(
    "/search",
    summary="Search for products in the catalog",
)
async def search_products(
    query: str = Query(..., description="A search query to find matching products. Matches its name or any other property."),
    db: sqlite3.Connection = Depends(get_db),
):
    if not query:
        raise HTTPException(status_code=400, detail="Query parameter cannot be empty")
    q_lower = query.lower()
    cur = db.execute("SELECT data FROM products")
    results: List[Dict[str, Any]] = []
    for row in cur:
        try:
            product = json.loads(row["data"])
        except json.JSONDecodeError:
            continue  # skip corrupted entries
        # Check name
        if q_lower in str(product.get("name", "")).lower():
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)