import json
import os
import sqlite3
from typing import Any, Dict, List

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Extra

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("Environment variable APP_SECRET must be set")

DB_PATH = "db.sqlite3"
MAX_DATA_SIZE = 1_048_576  # 1 MB limit for uploaded JSON strings

# ----------------------------------------------------------------------
# Security
# ----------------------------------------------------------------------
bearer_scheme = HTTPBearer(auto_error=False)


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> None:
    """
    Simple bearer token authentication using the secret provided in APP_SECRET.
    All endpoints require a valid token.
    """
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing authentication token")
    if credentials.credentials != APP_SECRET:
        raise HTTPException(status_code=401, detail="Invalid authentication token")


# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    # check_same_thread=False allows usage across FastAPI's async workers
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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


# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(
    title="Product Catalog",
    version="1.0.11",
    dependencies=[Depends(verify_token)],  # Apply auth globally
)


@app.on_event("startup")
def startup_event() -> None:
    init_db()


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class ProductModel(BaseModel):
    name: str

    class Config:
        extra = Extra.allow  # allow arbitrary additional string fields


class UploadModel(BaseModel):
    name: str
    data: str  # JSON string representing the product


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------
def _store_product(product_dict: Dict[str, Any]) -> None:
    """
    Store or update a product in the SQLite database.
    The whole product (including additional fields) is saved as a JSON string.
    """
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
    """
    Retrieve a single product by name. Raises 404 if not found.
    """
    with get_db_connection() as conn:
        cur = conn.execute("SELECT data FROM products WHERE name = ?", (name,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return json.loads(row["data"])


def _load_all_products() -> List[Dict[str, Any]]:
    """
    Retrieve all products stored in the catalog.
    """
    with get_db_connection() as conn:
        cur = conn.execute("SELECT data FROM products")
        rows = cur.fetchall()
        return [json.loads(row["data"]) for row in rows]


# ----------------------------------------------------------------------
# API endpoints
# ----------------------------------------------------------------------
@app.post("/add", status_code=status.HTTP_201_CREATED)
def add_product(product: ProductModel) -> Dict[str, str]:
    """
    Add a new product with arbitrary string properties.
    """
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
def download_product(
    name: str = Query(..., description="The name of the product")
) -> Dict[str, str]:
    """
    Return the full product entry as a JSON string under the key "data".
    """
    product = _load_product(name)
    return {"data": json.dumps(product, ensure_ascii=False)}


@app.post("/upload")
def upload_product(upload: UploadModel) -> Dict[str, str]:
    """
    Replace the product data for the given name with the supplied JSON string.
    The JSON string must represent an object where all values are strings.
    """
    # Enforce size limit to mitigate DoS attacks
    if len(upload.data.encode("utf-8")) > MAX_DATA_SIZE:
        raise HTTPException(
            status_code=400,
            detail="Uploaded data exceeds size limit of 1 MB",
        )

    # Parse the JSON payload safely
    try:
        product_dict = json.loads(upload.data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in data field")

    if not isinstance(product_dict, dict):
        raise HTTPException(status_code=400, detail="Data must be a JSON object")

    # Ensure the name matches the payload's name field
    if product_dict.get("name") != upload.name:
        raise HTTPException(
            status_code=400,
            detail="Name in payload does not match name field",
        )

    # Validate that all values are strings (including the name)
    for key, value in product_dict.items():
        if not isinstance(value, str):
            raise HTTPException(
                status_code=400,
                detail=f"Field '{key}' must be a string",
            )

    _store_product(product_dict)
    return {"message": "Product updated successfully"}


@app.get("/search")
def search_products(
    query: str = Query(..., description="Search query")
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Search for products where the query matches the name or any string property.
    """
    if not query:
        raise HTTPException(status_code=400, detail="Query parameter cannot be empty")

    all_products = _load_all_products()
    lowered = query.lower()
    results: List[Dict[str, Any]] = []

    for prod in all_products:
        # Match against the product name
        if lowered in prod.get("name", "").lower():
            results.append(prod)
            continue

        # Match against any other string property
        for key, value in prod.items():
            if key == "name":
                continue
            if isinstance(value, str) and lowered in value.lower():
                results.append(prod)
                break

    return {"results": results}


# ----------------------------------------------------------------------
# Server entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)