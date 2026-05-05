import os
import json
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Body,
    Request,
    status,
    Depends,
)
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Extra, ValidationError, root_validator

# -------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET", "")
DB_PATH = "db.sqlite3"
MAX_BODY_SIZE = 1_048_576  # 1 MB max request body size
MAX_SEARCH_RESULTS = 1000   # hard cap for search results
DEFAULT_SEARCH_LIMIT = 100  # default number of results returned

# -------------------------------------------------------------------------
# FastAPI app
# -------------------------------------------------------------------------
app = FastAPI(
    title="Product Catalog",
    description="Provides product catalog allowing multiple unstructured properties to be given and then searched for.",
    version="1.0.11",
)


# -------------------------------------------------------------------------
# Database utilities
# -------------------------------------------------------------------------
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
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


# -------------------------------------------------------------------------
# Request size validation
# -------------------------------------------------------------------------
def enforce_body_size(request: Request) -> None:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_BODY_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Payload too large",
                )
        except ValueError:
            # If header is malformed, let the request proceed (will be caught later)
            pass


# -------------------------------------------------------------------------
# Pydantic models
# -------------------------------------------------------------------------
class AddProductModel(BaseModel):
    name: str

    class Config:
        extra = Extra.allow  # allow arbitrary extra fields

    @root_validator(pre=True)
    def ensure_extra_fields_are_strings(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        # All keys except 'name' must have string values
        for key, value in values.items():
            if key == "name":
                continue
            if not isinstance(value, str):
                raise ValueError(f"Extra field '{key}' must be a string")
        return values


class UploadModel(BaseModel):
    name: str
    data: str


# -------------------------------------------------------------------------
# Endpoint implementations
# -------------------------------------------------------------------------
@app.post(
    "/add",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_body_size)],
)
def add_product(payload: AddProductModel = Body(...)):
    product_dict = payload.dict()
    name = product_dict["name"]
    data_json = json.dumps(product_dict)

    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO products (name, data) VALUES (?, ?)
                ON CONFLICT(name) DO NOTHING
                """,
                (name, data_json),
            )
            if conn.total_changes == 0:
                # No row inserted -> conflict
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Product with this name already exists.",
                )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product with this name already exists.",
        )
    return {"detail": "Product successfully added"}


@app.get("/download")
def download_product(name: str = Query(..., description="The name of the product")):
    with get_connection() as conn:
        cur = conn.execute("SELECT data FROM products WHERE name = ?", (name,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Product not found")
        data_json = row["data"]
    return {"data": data_json}


@app.post(
    "/upload",
    dependencies=[Depends(enforce_body_size)],
)
def upload_product(payload: UploadModel = Body(...)):
    # Verify that the provided data is valid JSON object string
    try:
        product_data = json.loads(payload.data)
        if not isinstance(product_data, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid data format; must be JSON object string",
        )

    # Ensure any extra fields are strings (same rule as /add)
    for key, value in product_data.items():
        if key == "name":
            continue
        if not isinstance(value, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Extra field '{key}' must be a string",
            )

    # Ensure the name in the payload matches the name inside the data (if present)
    inner_name = product_data.get("name")
    if inner_name and inner_name != payload.name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name mismatch between payload and data",
        )

    # Store the data (as JSON string) under the given name, replacing any existing entry
    data_json = json.dumps(product_data)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO products (name, data) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET data=excluded.data
            """,
            (payload.name, data_json),
        )
        conn.commit()
    return {"detail": "The product with the given name was updated."}


@app.get("/search")
def search_products(
    query: str = Query(..., description="A search query to find matching products. Matches its name or any other property."),
    limit: int = Query(
        DEFAULT_SEARCH_LIMIT,
        ge=1,
        le=MAX_SEARCH_RESULTS,
        description="Maximum number of results to return (capped at 1000).",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Number of matching results to skip (for pagination).",
    ),
):
    query_lc = query.lower()
    like_pattern = f"%{query_lc}%"
    results: List[Dict[str, Any]] = []

    with get_connection() as conn:
        # Search in name column directly and also in the JSON payload string (case‑insensitive)
        cur = conn.execute(
            """
            SELECT data FROM products
            WHERE lower(name) LIKE ?
               OR lower(data) LIKE ?
            LIMIT ? OFFSET ?
            """,
            (like_pattern, like_pattern, limit, offset),
        )
        rows = cur.fetchall()
        for row in rows:
            product_dict = json.loads(row["data"])
            results.append(product_dict)

    return {"results": results}


# -------------------------------------------------------------------------
# Exception handlers
# -------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": exc.errors()},
    )


@app.exception_handler(ValidationError)
async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": exc.errors()},
    )


# -------------------------------------------------------------------------
# Run the application
# -------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)