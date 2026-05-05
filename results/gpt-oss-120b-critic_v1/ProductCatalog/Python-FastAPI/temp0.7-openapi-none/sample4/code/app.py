import os
import json
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Query, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, Extra, model_validator

# Constants
DATABASE_PATH = "db.sqlite3"
MAX_BODY_SIZE = 1_048_576  # 1 MB
MAX_SEARCH_RESULTS = 1000

app = FastAPI(title="Product Catalog", version="1.0.11")


def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with sqlite3.connect(DATABASE_PATH) as conn:
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
def startup_event() -> None:
    init_db()


def enforce_body_size(request: Request) -> None:
    """
    Simple request‑size guard based on the Content‑Length header.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            length = int(content_length)
            if length > MAX_BODY_SIZE:
                raise HTTPException(
                    status_code=413, detail="Payload too large"
                )
        except ValueError:
            # If header is malformed we fall back to allowing the request;
            # FastAPI will still raise an error if it cannot parse the body.
            pass


class AddProductModel(BaseModel):
    name: str = Field(..., description="The name of the product")

    class Config:
        extra = Extra.allow  # accept any additional fields

    @model_validator(mode="after")
    def ensure_extra_strings(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """
        All additional properties must be strings, as required by the OpenAPI spec.
        """
        for key, value in values.items():
            if key != "name" and not isinstance(value, str):
                raise ValueError(f"Additional property '{key}' must be a string")
        return values


class UploadModel(BaseModel):
    name: str = Field(..., description="The name of the product")
    data: str = Field(..., description="The data of a product (JSON string)")


def product_to_dict(product_json: str) -> Dict[str, Any]:
    """
    Convert stored JSON string to dict. Propagates JSON errors as HTTP 500.
    """
    try:
        return json.loads(product_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500, detail=f"Corrupted product data: {exc}"
        ) from exc


def validate_product_dict(product: Dict[str, Any]) -> None:
    """
    Ensure that every value (except 'name') is a string.
    """
    for key, value in product.items():
        if key == "name":
            continue
        if not isinstance(value, str):
            raise ValueError(f"Property '{key}' must be a string")


@app.post(
    "/add",
    status_code=201,
    summary="Add a new product to the catalog",
)
def add_product(
    payload: AddProductModel,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
):
    enforce_body_size(request)

    product_dict = payload.model_dump()
    # Validate extra fields are strings (already done by model validator)
    product_json = json.dumps(product_dict)

    try:
        db.execute(
            "INSERT INTO products (name, data) VALUES (?, ?)",
            (product_dict["name"], product_json),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=400, detail="Product with this name already exists"
        )
    return JSONResponse(content={"message": "Product added"})


@app.get(
    "/download",
    summary="Download the entire current catalog for a given product name",
)
def download_product(
    name: str = Query(..., description="The name of the product"),
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Returns the whole catalog as a JSON string inside the ``data`` field.
    The ``name`` query parameter is kept for compatibility; if the name does
    not exist in the catalog a ``400`` error is returned.
    """
    # Verify the requested name exists
    cur = db.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    if not cur.fetchone():
        raise HTTPException(status_code=400, detail="Product name not found")

    # Fetch all products
    cur = db.execute("SELECT data FROM products")
    all_products = [product_to_dict(row["data"]) for row in cur.fetchall()]

    catalog_json = json.dumps(all_products)
    return {"data": catalog_json}


@app.post(
    "/upload",
    summary="Upload data for a product, replacing any previous data",
)
def upload_product(
    payload: UploadModel,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
):
    enforce_body_size(request)

    # Validate that payload.data is a valid JSON string
    try:
        product_obj = json.loads(payload.data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in data field")

    if not isinstance(product_obj, dict):
        raise HTTPException(status_code=400, detail="Data must be a JSON object")

    # Ensure only string values for additional properties
    try:
        validate_product_dict(product_obj)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Optional name consistency check
    inner_name = product_obj.get("name")
    if inner_name and inner_name != payload.name:
        raise HTTPException(
            status_code=400,
            detail="Name mismatch between payload and data",
        )

    # Use INSERT OR REPLACE for maximum SQLite compatibility
    db.execute(
        """
        INSERT OR REPLACE INTO products (name, data) VALUES (?, ?)
        """,
        (payload.name, payload.data),
    )
    db.commit()
    return {"message": "Product updated"}


@app.get(
    "/search",
    summary="Search for products in the catalog",
)
def search_products(
    query: str = Query(..., description="A search query to find matching products"),
    db: sqlite3.Connection = Depends(get_db),
):
    if not query:
        raise HTTPException(status_code=400, detail="Query parameter cannot be empty")
    query_lower = query.lower()
    cur = db.execute("SELECT data FROM products")
    results: List[Dict[str, Any]] = []

    for row in cur.fetchall():
        if len(results) >= MAX_SEARCH_RESULTS:
            break  # enforce result size limit

        product_dict = product_to_dict(row["data"])

        # Name match
        name = product_dict.get("name", "")
        if query_lower in str(name).lower():
            results.append(product_dict)
            continue

        # Other fields match (string values only)
        for key, value in product_dict.items():
            if key == "name":
                continue
            if isinstance(value, str) and query_lower in value.lower():
                results.append(product_dict)
                break

    return {"results": results}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)