import json
import os
import sqlite3
from typing import Any, Dict, List

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    status,
    Header,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, RootModel, ValidationError, field_validator

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET", "").strip()
MAX_REQUEST_SIZE = 1 * 1024 * 1024  # 1 MB

DB_PATH = "db.sqlite3"

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


init_db()


# ----------------------------------------------------------------------
# Security dependency
# ----------------------------------------------------------------------
def verify_api_key(x_api_key: str = Header(default="")) -> None:
    """
    Simple API‑key check. If APP_SECRET is defined, the request must provide
    the matching key in the X-API-Key header. Otherwise the check is bypassed.
    """
    if APP_SECRET and x_api_key != APP_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


# ----------------------------------------------------------------------
# Request size limiting middleware
# ----------------------------------------------------------------------
async def limit_request_size(request: Request, call_next):
    body = await request.body()
    if len(body) > MAX_REQUEST_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Request body too large",
        )
    # Re‑inject the body for downstream handlers
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive  # type: ignore
    response = await call_next(request)
    return response


# ----------------------------------------------------------------------
# Pydantic models with strict string‑only additional properties
# ----------------------------------------------------------------------
def _ensure_string_properties(data: Dict[str, Any]) -> None:
    """
    Validate that all values in the dict (except the mandatory 'name')
    are strings. Raises HTTPException(400) on failure.
    """
    for key, value in data.items():
        if key == "name":
            continue
        if not isinstance(value, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Property '{key}' must be a string",
            )


class AddProductModel(BaseModel):
    name: str

    model_config = {"extra": "allow"}

    @field_validator("*", mode="before")
    @classmethod
    def validate_extra_strings(cls, v: Any, info):
        # This validator runs for every field, including extra ones.
        # For extra fields, `info.field_name` will be the key name.
        if info.field_name != "name" and not isinstance(v, str):
            raise ValueError("All additional properties must be strings")
        return v

    @classmethod
    def __get_validators__(cls):
        # Ensure the whole dict is validated for extra string properties
        yield from super().__get_validators__()
        yield cls._validate_extra_properties

    @classmethod
    def _validate_extra_properties(cls, values: Dict[str, Any]):
        _ensure_string_properties(values)
        return values


class UploadModel(BaseModel):
    name: str
    data: str


# ----------------------------------------------------------------------
# Core helper functions
# ----------------------------------------------------------------------
def store_product(name: str, data_dict: Dict[str, Any]) -> None:
    """
    Store or replace a product. The dict must already be validated.
    """
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


# ----------------------------------------------------------------------
# FastAPI application
# ----------------------------------------------------------------------
app = FastAPI(title="Product Catalog", version="1.0.11")
app.middleware("http")(limit_request_size)


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/add",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_api_key)],
    responses={201: {"description": "Product successfully added"}, 400: {"description": "Invalid input"}},
)
async def add_product(payload: AddProductModel):
    product_dict = payload.model_dump()
    name = product_dict.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    # Validation of extra fields already performed by the model
    store_product(name, product_dict)
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"detail": "Product added"})


@app.get(
    "/download",
    dependencies=[Depends(verify_api_key)],
    responses={200: {"description": "The catalog file"}},
)
async def download_product(name: str = Query(..., description="The name of the product")):
    product = fetch_product(name)
    # Return the whole product dict as a JSON string in the 'data' field
    return {"data": json.dumps(product, ensure_ascii=False)}


@app.post(
    "/upload",
    dependencies=[Depends(verify_api_key)],
    responses={200: {"description": "The product with the given name was updated."}, 400: {"description": "Invalid upload query"}},
)
async def upload_product(payload: UploadModel):
    try:
        product_data = json.loads(payload.data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in data field")
    if not isinstance(product_data, dict):
        raise HTTPException(status_code=400, detail="Data must be a JSON object")
    # Ensure all additional properties are strings
    _ensure_string_properties(product_data)
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


@app.get(
    "/search",
    dependencies=[Depends(verify_api_key)],
    responses={200: {"description": "A list of matching products"}, 400: {"description": "Invalid search query"}},
)
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