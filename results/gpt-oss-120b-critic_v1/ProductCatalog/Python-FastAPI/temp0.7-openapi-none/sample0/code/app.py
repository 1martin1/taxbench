import os
import json
import sqlite3
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request, Depends, Query, status, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Extra, Field, ValidationError, validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DATABASE_PATH = "db.sqlite3"
MAX_BODY_SIZE = 1 * 1024 * 1024          # 1 MB
MAX_STRING_LENGTH = 1000                 # Max length for any string field
MAX_SEARCH_RESULTS = 1000                # Upper bound for search result set

# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(
    title="Product Catalog",
    description="Provides product catalog allowing multiple unstructured properties to be given and then searched for.",
    version="1.0.11",
)


# ----------------------------------------------------------------------
# Middleware to limit request body size
# ----------------------------------------------------------------------
class BodySizeLimiter(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, max_body_size: int = MAX_BODY_SIZE):
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_size:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Request body too large",
                    )
            except ValueError:
                pass  # ignore malformed header, let the handler deal with it

        # For safety, also enforce size while reading the body
        body = await request.body()
        if len(body) > self.max_body_size:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Request body too large",
            )
        # Replace the request's stream with the already read body
        request = Request(request.scope, receive=lambda: {"type": "http.request", "body": body, "more_body": False})
        return await call_next(request)


app.add_middleware(BodySizeLimiter)


# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
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


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ----------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------
def get_db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Pydantic models with validation
# ----------------------------------------------------------------------
class ProductIn(BaseModel):
    """
    Model for /add endpoint.
    Allows arbitrary additional string fields.
    """
    name: str = Field(..., max_length=MAX_STRING_LENGTH)

    model_config = ConfigDict(extra=Extra.allow)

    @validator("*", pre=True, each_item=False)
    def check_string_lengths(cls, v, field):
        if isinstance(v, str) and len(v) > MAX_STRING_LENGTH:
            raise ValueError(f"Field '{field.name}' exceeds maximum length of {MAX_STRING_LENGTH}")
        return v

    def as_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class UploadModel(BaseModel):
    name: str = Field(..., max_length=MAX_STRING_LENGTH)
    data: str  # JSON string representing a product

    @validator("data")
    def check_data_size(cls, v):
        if len(v) > MAX_BODY_SIZE:
            raise ValueError("Data field exceeds maximum allowed size")
        return v


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post("/add", status_code=status.HTTP_201_CREATED)
def add_product(
    product: ProductIn,
    db: sqlite3.Connection = Depends(get_db),
):
    product_dict = product.as_dict()
    name = product_dict["name"]

    # Ensure all values are strings and respect length limits
    for key, value in product_dict.items():
        if not isinstance(value, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"All property values must be strings. Invalid value for '{key}'.",
            )
        if len(value) > MAX_STRING_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Value for '{key}' exceeds maximum length of {MAX_STRING_LENGTH}.",
            )

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
            detail="Product with this name already exists.",
        )

    # Return an empty response body as the OpenAPI spec does not define a response schema
    return Response(status_code=status.HTTP_201_CREATED)


@app.get("/download")
def download_product(name: str = Query(..., description="The name of the product")):
    conn = get_connection()
    try:
        cur = conn.execute("SELECT data FROM products WHERE name = ?", (name,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found.",
            )
        data_str = row["data"]
        return {"data": data_str}
    finally:
        conn.close()


@app.post("/upload")
def upload_product(
    payload: UploadModel,
    db: sqlite3.Connection = Depends(get_db),
):
    # Parse the 'data' field which should be a JSON string representing a product
    try:
        product_dict = json.loads(payload.data)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON in 'data' field.",
        )
    if not isinstance(product_dict, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'data' must represent a JSON object.",
        )

    # Validate presence and consistency of name
    if "name" not in product_dict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded data must contain a 'name' field.",
        )
    if product_dict["name"] != payload.name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name in payload does not match name in data.",
        )

    # Ensure all values are strings and respect length limits
    for k, v in product_dict.items():
        if not isinstance(v, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"All property values must be strings. Invalid value for '{k}'.",
            )
        if len(v) > MAX_STRING_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Value for '{k}' exceeds maximum length of {MAX_STRING_LENGTH}.",
            )

    data_json = json.dumps(product_dict, ensure_ascii=False)
    db.execute(
        """
        INSERT INTO products (name, data) VALUES (?, ?)
        ON CONFLICT(name) DO UPDATE SET data=excluded.data
        """,
        (payload.name, data_json),
    )
    db.commit()
    return {"message": "Product updated."}


@app.get("/search")
def search_products(
    query: str = Query(..., description="A search query to find matching products. Matches its name or any other property.")
):
    conn = get_connection()
    try:
        lowered_query = query.lower()
        like_pattern = f"%{lowered_query}%"
        cur = conn.execute(
            """
            SELECT data FROM products
            WHERE LOWER(name) LIKE ?
               OR LOWER(data) LIKE ?
            LIMIT ?
            """,
            (like_pattern, like_pattern, MAX_SEARCH_RESULTS),
        )
        results: List[Dict[str, Any]] = []
        for row in cur:
            try:
                product_dict = json.loads(row["data"])
            except json.JSONDecodeError:
                # Skip malformed entries (should not happen)
                continue
            results.append(product_dict)
        return {"results": results}
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)