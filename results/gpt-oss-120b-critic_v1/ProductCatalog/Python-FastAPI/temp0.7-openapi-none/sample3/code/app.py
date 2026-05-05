import os
import json
import sqlite3
from typing import Any, Dict, List, Generator, Optional

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Body,
    Depends,
    Request,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, root_validator, ValidationError

# --------------------------------------------------------------------------- #
# Configuration constants (security & resource limits)
# --------------------------------------------------------------------------- #
DB_PATH = "db.sqlite3"

MAX_NAME_LENGTH = 100
MAX_KEY_LENGTH = 50
MAX_VALUE_LENGTH = 500
MAX_PROPERTIES = 100  # number of additional arbitrary fields
MAX_DATA_SIZE = 1_048_576  # 1 MB for uploaded JSON string
MAX_SEARCH_QUERY_LENGTH = 100
MAX_SEARCH_LIMIT = 100  # max items returned per request
DEFAULT_SEARCH_LIMIT = 20

# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
app = FastAPI(title="Product Catalog", version="1.0.11")

# --------------------------------------------------------------------------- #
# Database utilities
# --------------------------------------------------------------------------- #
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Provide a new SQLite connection per request.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """
    Initialise the SQLite database with the required table.
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
def on_startup() -> None:
    init_db()


# --------------------------------------------------------------------------- #
# Pydantic models with validation
# --------------------------------------------------------------------------- #
class ProductIn(BaseModel):
    """
    Model for the /add endpoint.
    Allows arbitrary additional string fields but enforces length/size limits.
    """
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., description="The name of the product", max_length=MAX_NAME_LENGTH)

    @root_validator(pre=True)
    def check_additional_fields(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        # Remove the required 'name' field from the additional count
        additional_items = {k: v for k, v in values.items() if k != "name"}
        if len(additional_items) > MAX_PROPERTIES:
            raise ValueError(f"Too many additional properties (max {MAX_PROPERTIES})")
        for key, value in additional_items.items():
            if not isinstance(key, str):
                raise ValueError("All property keys must be strings")
            if len(key) > MAX_KEY_LENGTH:
                raise ValueError(f"Property key '{key}' exceeds maximum length of {MAX_KEY_LENGTH}")
            if not isinstance(value, str):
                raise ValueError(f"Property '{key}' must be a string")
            if len(value) > MAX_VALUE_LENGTH:
                raise ValueError(f"Property '{key}' exceeds maximum length of {MAX_VALUE_LENGTH}")
        return values


class UploadPayload(BaseModel):
    """
    Model for the /upload endpoint.
    """
    name: str = Field(..., description="The name of the product", max_length=MAX_NAME_LENGTH)
    data: str = Field(..., description="The data of a product")

    @root_validator
    def validate_data(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        data_str = values.get("data", "")
        if len(data_str.encode("utf-8")) > MAX_DATA_SIZE:
            raise ValueError(f"Data size exceeds maximum of {MAX_DATA_SIZE} bytes")
        try:
            parsed = json.loads(data_str)
        except json.JSONDecodeError as exc:
            raise ValueError("Data must be a valid JSON object string") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Data JSON must represent an object")
        # Enforce same constraints as ProductIn on the parsed object
        name_in_data = parsed.get("name")
        if name_in_data and name_in_data != values.get("name"):
            raise ValueError("Name field inside data does not match the payload name")
        # Validate fields inside the JSON object
        extra = {k: v for k, v in parsed.items() if k != "name"}
        if len(extra) > MAX_PROPERTIES:
            raise ValueError(f"Too many properties in uploaded data (max {MAX_PROPERTIES})")
        for key, value in extra.items():
            if not isinstance(key, str):
                raise ValueError("All property keys must be strings")
            if len(key) > MAX_KEY_LENGTH:
                raise ValueError(f"Property key '{key}' exceeds maximum length of {MAX_KEY_LENGTH}")
            if not isinstance(value, str):
                raise ValueError(f"Property '{key}' must be a string")
            if len(value) > MAX_VALUE_LENGTH:
                raise ValueError(f"Property '{key}' exceeds maximum length of {MAX_VALUE_LENGTH}")
        return values


# --------------------------------------------------------------------------- #
# Endpoint implementations
# --------------------------------------------------------------------------- #
@app.post(
    "/add",
    status_code=status.HTTP_201_CREATED,
    summary="Add a new product to the catalog",
)
def add_product(
    request: Request,
    payload: ProductIn = Body(..., description="JSON object containing product details"),
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Insert a new product. Reject if name already exists.
    """
    # Enforce request size limit (fastapi already parses JSON; we limit field lengths already)
    product_dict = payload.model_dump()
    name = product_dict["name"]

    cur = db.execute("SELECT 1 FROM products WHERE name = ?", (name,))
    if cur.fetchone():
        raise HTTPException(status_code=400, detail="Product with this name already exists")

    data_json = json.dumps(product_dict, ensure_ascii=False)
    db.execute("INSERT INTO products (name, data) VALUES (?, ?)", (name, data_json))
    db.commit()
    return JSONResponse(content={"message": "Product added"}, status_code=201)


@app.get(
    "/download",
    summary="Download the entire current catalog entry for a given product name",
    response_model=dict,
)
def download_product(
    name: str = Query(..., description="The name of the product", max_length=MAX_NAME_LENGTH),
    db: sqlite3.Connection = Depends(get_db),
):
    cur = db.execute("SELECT data FROM products WHERE name = ?", (name,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"data": row["data"]}


@app.post(
    "/upload",
    summary="Upload data for a product, replacing any previous data",
    response_model=dict,
)
def upload_product(
    request: Request,
    payload: UploadPayload = Body(..., description="The catalog file"),
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Replace (or insert) a product's data with the provided JSON string.
    """
    # Validation already performed by UploadPayload
    db.execute(
        """
        INSERT INTO products (name, data) VALUES (?, ?)
        ON CONFLICT(name) DO UPDATE SET data=excluded.data
        """,
        (payload.name, payload.data),
    )
    db.commit()
    return {"message": "Product updated"}


@app.get(
    "/search",
    summary="Search for products in the catalog",
    response_model=dict,
)
def search_products(
    query: str = Query(..., description="Search query", max_length=MAX_SEARCH_QUERY_LENGTH),
    limit: int = Query(
        DEFAULT_SEARCH_LIMIT,
        ge=1,
        le=MAX_SEARCH_LIMIT,
        description=f"Maximum number of results to return (1-{MAX_SEARCH_LIMIT})",
    ),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Search for products where the name or any stored JSON field contains the query string.
    The search is case‑insensitive and performed directly in SQLite to avoid loading
    the entire catalog into memory.
    """
    pattern = f"%{query}%"
    sql = """
        SELECT data FROM products
        WHERE LOWER(name) LIKE LOWER(?)
           OR LOWER(data) LIKE LOWER(?)
        LIMIT ? OFFSET ?
    """
    cur = db.execute(sql, (pattern, pattern, limit, offset))
    rows = cur.fetchall()
    results: List[Dict[str, Any]] = []
    for row in rows:
        try:
            product = json.loads(row["data"])
            results.append(product)
        except json.JSONDecodeError:
            # Skip malformed entries silently
            continue
    return {"results": results}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)