import os
import json
import sqlite3
from typing import Any, Dict, List

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Body,
    Depends,
    Request,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, root_validator

# --------------------------------------------------------------------------- #
# Configuration constants
# --------------------------------------------------------------------------- #
DB_PATH = "db.sqlite3"
MAX_BODY_SIZE = 1 * 1024 * 1024          # 1 MB
MAX_QUERY_LEN = 256                      # Max length for search query
MAX_SEARCH_RESULTS = 100                 # Upper bound on returned search items

# --------------------------------------------------------------------------- #
# FastAPI app initialization
# --------------------------------------------------------------------------- #
app = FastAPI(title="Product Catalog", version="1.0.11")


# --------------------------------------------------------------------------- #
# Database utilities
# --------------------------------------------------------------------------- #
def get_db():
    """Dependency that provides a SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create the products table if it does not exist."""
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


# Initialize DB at import time
init_db()


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class ProductIn(BaseModel):
    name: str = Field(..., description="The name of the product")

    class Config:
        extra = "allow"

    @root_validator(pre=True)
    def ensure_string_values(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """Make sure all additional fields are strings."""
        for key, val in values.items():
            if key == "name":
                continue
            if not isinstance(val, str):
                raise ValueError(f"Additional field '{key}' must be a string.")
        return values


class UploadModel(BaseModel):
    name: str = Field(..., description="The name of the product")
    data: str = Field(..., description="The data of a product (JSON string)")


# --------------------------------------------------------------------------- #
# Middleware: request body size limiting
# --------------------------------------------------------------------------- #
@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    body = await request.body()
    if len(body) > MAX_BODY_SIZE:
        return JSONResponse(
            status_code=413,
            content={"detail": "Request body too large"},
        )
    # The body is cached in the request object, so downstream handlers can still read it.
    response = await call_next(request)
    return response


# --------------------------------------------------------------------------- #
# Exception handling: convert validation errors to 400 Bad Request
# --------------------------------------------------------------------------- #
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": exc.errors()},
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.post(
    "/add",
    status_code=status.HTTP_201_CREATED,
    summary="Add a new product to the catalog",
)
def add_product(
    payload: ProductIn = Body(..., description="JSON object containing product details"),
    db: sqlite3.Connection = Depends(get_db),
):
    product_dict = payload.model_dump()
    name = product_dict["name"]
    data_json = json.dumps(product_dict, ensure_ascii=False)

    try:
        db.execute(
            "INSERT INTO products (name, data) VALUES (?, ?)",
            (name, data_json),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product with name '{name}' already exists.",
        )
    return {"message": "Product added successfully"}


@app.get(
    "/download",
    summary="Download the entire current catalog for a given product name",
)
def download_product(
    name: str = Query(..., description="The name of the product"),
    db: sqlite3.Connection = Depends(get_db),
):
    cur = db.execute("SELECT data FROM products WHERE name = ?", (name,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with name '{name}' not found.",
        )
    return {"data": row["data"]}


@app.post(
    "/upload",
    summary="Upload data for a product, replacing any existing entry",
)
def upload_product(
    payload: UploadModel = Body(..., description="The catalog file"),
    db: sqlite3.Connection = Depends(get_db),
):
    # Validate that `data` is a proper JSON string
    try:
        data_dict = json.loads(payload.data)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Field 'data' must be a valid JSON string.",
        )

    # Ensure that the JSON contains a 'name' field and that it matches the provided name
    json_name = data_dict.get("name")
    if json_name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded JSON must contain a 'name' field.",
        )
    if json_name != payload.name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The name in the JSON data does not match the provided name.",
        )

    # Store the JSON string as-is (it already includes all properties)
    db.execute(
        """
        INSERT INTO products (name, data) VALUES (?, ?)
        ON CONFLICT(name) DO UPDATE SET data=excluded.data
        """,
        (payload.name, payload.data),
    )
    db.commit()
    return {"message": "Product updated successfully"}


@app.get(
    "/search",
    summary="Search for products in the catalog",
)
def search_products(
    query: str = Query(..., description="A search query to find matching products."),
    db: sqlite3.Connection = Depends(get_db),
):
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter cannot be empty.",
        )
    if len(query) > MAX_QUERY_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Query length must not exceed {MAX_QUERY_LEN} characters.",
        )

    pattern = f"%{query}%"
    cur = db.execute(
        """
        SELECT data FROM products
        WHERE name LIKE ? OR data LIKE ?
        LIMIT ?
        """,
        (pattern, pattern, MAX_SEARCH_RESULTS),
    )
    results: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        try:
            product = json.loads(row["data"])
        except json.JSONDecodeError:
            continue  # skip malformed rows
        results.append(product)

    return {"results": results}


# --------------------------------------------------------------------------- #
# Application entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, log_level="info")