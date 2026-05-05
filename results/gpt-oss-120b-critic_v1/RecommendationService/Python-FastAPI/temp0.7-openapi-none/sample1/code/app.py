import os
import sqlite3
import threading
import html
from typing import List

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, validator
from starlette.middleware.base import BaseHTTPMiddleware

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = "db.sqlite3"
MAX_TAGS_PER_REQUEST = 20
MAX_TAG_LENGTH = 30
MAX_PRODUCT_NAME_LENGTH = 200
MAX_TAGS_IN_POST = 20

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

# Global connection shared across threads (check_same_thread=False)
_global_db = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
_global_db.row_factory = sqlite3.Row

# Enable WAL mode for better concurrency
with _global_db:
    _global_db.execute("PRAGMA journal_mode=WAL;")
    _global_db.execute("PRAGMA foreign_keys=ON;")

# Thread lock to serialize write operations
_db_lock = threading.Lock()


def init_db() -> None:
    with _global_db:
        _global_db.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )
            """
        )
        _global_db.execute(
            """
            CREATE TABLE IF NOT EXISTS product_tags (
                product_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            )
            """
        )


init_db()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ProductCreate(BaseModel):
    product_name: str = Field(
        ...,
        max_length=MAX_PRODUCT_NAME_LENGTH,
        description="Name of the product",
    )
    tags: List[str] = Field(
        ...,
        min_items=1,
        max_items=MAX_TAGS_IN_POST,
        description="List of tags for the product",
    )

    @validator("product_name")
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("product_name must not be empty")
        return v

    @validator("tags", each_item=True)
    def tag_constraints(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("tags must not contain empty strings")
        if len(v) > MAX_TAG_LENGTH:
            raise ValueError(f"tag length must not exceed {MAX_TAG_LENGTH} characters")
        return v


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)


# Middleware to attach the global DB connection to each request
class DBSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.db = _global_db
        response = await call_next(request)
        return response


app.add_middleware(DBSessionMiddleware)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def insert_product(db: sqlite3.Connection, name: str, tags: List[str]) -> int:
    with _db_lock:
        cursor = db.cursor()
        cursor.execute("INSERT INTO products (name) VALUES (?)", (name,))
        product_id = cursor.lastrowid
        cursor.executemany(
            "INSERT INTO product_tags (product_id, tag) VALUES (?, ?)",
            [(product_id, tag) for tag in tags],
        )
        db.commit()
    return product_id


def query_products_by_tags(db: sqlite3.Connection, tags: List[str]) -> List[sqlite3.Row]:
    placeholders = ",".join("?" for _ in tags)
    query = f"""
        SELECT DISTINCT p.id, p.name
        FROM products p
        JOIN product_tags t ON p.id = t.product_id
        WHERE t.tag IN ({placeholders})
        ORDER BY p.id ASC
    """
    cursor = db.cursor()
    cursor.execute(query, tags)
    return cursor.fetchall()


def build_html_response(products: List[sqlite3.Row]) -> str:
    html_parts = [
        "<html>",
        "<head><title>Product Recommendations</title></head>",
        "<body>",
        "<h1>Recommended Products</h1>",
    ]
    if not products:
        html_parts.append("<p>No products match the provided tags.</p>")
    else:
        html_parts.append("<ul>")
        for prod in products:
            safe_name = html.escape(prod["name"])
            html_parts.append(f"<li>{safe_name}</li>")
        html_parts.append("</ul>")
    html_parts.append("</body></html>")
    return "\n".join(html_parts)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Convert FastAPI's 422 validation errors to 400 as per OpenAPI spec
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": exc.errors()},
    )


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.post("/recommender", status_code=status.HTTP_200_OK)
def post_product(product: ProductCreate, request: Request):
    """
    Post a new product along with its tags.
    """
    db: sqlite3.Connection = request.state.db
    try:
        insert_product(db, product.product_name, product.tags)
    except sqlite3.IntegrityError:
        # Do not expose internal DB details
        raise HTTPException(status_code=400, detail="Failed to add product due to database error")
    return {"detail": "Product added successfully"}


@app.get(
    "/recommender",
    response_class=HTMLResponse,
    status_code=status.HTTP_200_OK,
)
def get_recommendations(
    tags: str = Query(..., description="Tags to search for separated by comma."),
    request: Request = None,
):
    """
    Get all posted products that match at least one of the provided tags.
    Returns an HTML page with the matching products.
    """
    # Parse and validate tags
    raw_tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not raw_tags:
        raise HTTPException(
            status_code=400, detail="At least one non‑empty tag must be provided"
        )
    if len(raw_tags) > MAX_TAGS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Number of tags must not exceed {MAX_TAGS_PER_REQUEST}",
        )
    for t in raw_tags:
        if len(t) > MAX_TAG_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Each tag must not exceed {MAX_TAG_LENGTH} characters",
            )

    db: sqlite3.Connection = request.state.db
    rows = query_products_by_tags(db, raw_tags)
    html_content = build_html_response(rows)
    return HTMLResponse(content=html_content, media_type="text/html")


# ---------------------------------------------------------------------------
# Run the application
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)