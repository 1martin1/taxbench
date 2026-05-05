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
    Header,
    Request,
    status,
)
from fastapi.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field, Extra, constr

# -------------------- Configuration --------------------
DB_PATH = "db.sqlite3"
MAX_PAYLOAD_SIZE = 1 * 1024 * 1024  # 1 MB
MAX_FIELD_LENGTH = 255  # Maximum length for any string field (including name)

# -------------------- Database utilities --------------------
def get_connection():
    # check_same_thread=False allows usage across FastAPI's thread pool
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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

# -------------------- Security --------------------
def verify_token(
    authorization: str = Header(None, description="Bearer token for authentication")
):
    """
    Simple token authentication. If APP_SECRET env var is set, the request must contain
    an Authorization header with a matching Bearer token. If APP_SECRET is not set,
    the endpoint is open (maintains backward compatibility).
    """
    secret = os.getenv("APP_SECRET")
    if not secret:
        return  # No secret configured; allow unauthenticated access

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    token = authorization.split(" ", 1)[1]
    if token != secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


# -------------------- Request size limiting middleware --------------------
class LimitUploadSizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Read the body up to the limit
        body = await request.body()
        if len(body) > MAX_PAYLOAD_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Request payload exceeds {MAX_PAYLOAD_SIZE // 1024}KB limit.",
            )
        # Replace the request's stream with the already read body
        request._receive = lambda: {"type": "http.request", "body": body, "more_body": False}
        response = await call_next(request)
        return response


# -------------------- Pydantic models --------------------
class ProductModel(BaseModel):
    name: constr(max_length=MAX_FIELD_LENGTH) = Field(..., description="The name of the product")

    class Config:
        extra = Extra.allow  # allow arbitrary additional string fields


class UploadModel(BaseModel):
    name: constr(max_length=MAX_FIELD_LENGTH) = Field(..., description="The name of the product")
    data: str = Field(..., description="The data of a product (JSON string)")


# -------------------- FastAPI app --------------------
app = FastAPI(title="Product Catalog", version="1.0.11")
app.add_middleware(LimitUploadSizeMiddleware)


# -------------------- Endpoints --------------------
@app.post("/add", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_token)])
def add_product(product: ProductModel = Body(...)):
    """
    Add a new product with arbitrary string properties.
    """
    product_dict: Dict[str, Any] = product.model_dump()
    # Validate that all values are strings and respect length limits
    for key, value in product_dict.items():
        if not isinstance(value, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"All property values must be strings. Property '{key}' is not.",
            )
        if len(value) > MAX_FIELD_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Value for property '{key}' exceeds maximum length of {MAX_FIELD_LENGTH}.",
            )
    name = product_dict["name"]
    data_json = json.dumps(product_dict)

    conn = get_connection()
    try:
        cur = conn.execute("SELECT 1 FROM products WHERE name = ?", (name,))
        if cur.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product with name '{name}' already exists.",
            )
        conn.execute(
            "INSERT INTO products (name, data) VALUES (?, ?)",
            (name, data_json),
        )
        conn.commit()
    finally:
        conn.close()
    return {"message": "Product added successfully."}


@app.get("/download", dependencies=[Depends(verify_token)])
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
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with name '{name}' not found.",
            )
        data = row["data"]
    finally:
        conn.close()
    return {"data": data}


@app.post("/upload", dependencies=[Depends(verify_token)])
def upload_product(upload: UploadModel = Body(...)):
    """
    Upload data for a product; replaces any existing data for that product.
    """
    # Validate that `data` is a proper JSON string
    try:
        data_dict = json.loads(upload.data)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provided data is not valid JSON.",
        )
    if not isinstance(data_dict, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provided data JSON must be an object.",
        )
    # Ensure the name inside the JSON (if present) matches the path name
    inner_name = data_dict.get("name")
    if inner_name and inner_name != upload.name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name inside data does not match the provided name.",
        )
    # Validate all fields are strings and respect length limits
    for k, v in data_dict.items():
        if not isinstance(v, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"All property values must be strings. Property '{k}' is not.",
            )
        if len(v) > MAX_FIELD_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Value for property '{k}' exceeds maximum length of {MAX_FIELD_LENGTH}.",
            )
    # Store the raw JSON string (as provided) to preserve formatting
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO products (name, data) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET data=excluded.data
            """,
            (upload.name, upload.data),
        )
        conn.commit()
    finally:
        conn.close()
    return {"message": f"Product '{upload.name}' updated successfully."}


@app.get("/search", dependencies=[Depends(verify_token)])
def search_products(query: str = Query(..., description="Search query matching name or any property")):
    """
    Search for products whose name or any property contains the query string (case‑insensitive).
    """
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter cannot be empty.",
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
            # Check each string value for the query substring
            if any(isinstance(v, str) and lowered in v.lower() for v in product_dict.values()):
                results.append(product_dict)
    finally:
        conn.close()
    return {"results": results}


# -------------------- Run server --------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=5000,
        log_level="info",
    )