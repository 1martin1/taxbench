import os
import sqlite3
import html
import logging
from typing import List

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, conlist, constr, validator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATABASE_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set for secure configuration.")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database utilities
# ---------------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    """Create a thread-safe SQLite connection."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """Create tables if they do not exist, handling possible I/O errors."""
    try:
        # Ensure the directory for the DB file exists and is writable
        db_dir = os.path.dirname(os.path.abspath(DATABASE_PATH))
        os.makedirs(db_dir, exist_ok=True)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS product_tags (
                product_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
    except Exception as exc:
        logger.exception("Failed to initialize the database.")
        raise RuntimeError(f"Database initialization error: {exc}") from exc
    finally:
        if "conn" in locals():
            conn.close()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
TagStr = constr(min_length=1, max_length=30, strip_whitespace=True)

class ProductCreate(BaseModel):
    product_name: constr(min_length=1, max_length=100, strip_whitespace=True) = Field(
        ..., description="Name of the product"
    )
    tags: conlist(TagStr, min_items=1, max_items=20) = Field(
        ..., description="List of tags for the product"
    )

    @validator("tags", each_item=True)
    def non_empty_tag(cls, v: str) -> str:
        # The constr type already ensures non‑empty after stripping
        return v

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)

# ---------------------------------------------------------------------------
# Startup event
# ---------------------------------------------------------------------------
@app.on_event("startup")
def on_startup() -> None:
    init_db()

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
MAX_QUERY_TAGS = 20  # Upper bound for tags in a single query

def insert_product(name: str, tags: List[str]) -> None:
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO products (name) VALUES (?)", (name,))
        product_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO product_tags (product_id, tag) VALUES (?, ?)",
            [(product_id, tag) for tag in tags],
        )
        conn.commit()
    finally:
        conn.close()

def query_products_by_tags(search_tags: List[str]) -> List[sqlite3.Row]:
    """
    Returns distinct products that have at least one matching tag.
    Limits the number of tags used in the query to prevent resource exhaustion.
    """
    if not search_tags:
        return []

    # Enforce maximum number of tags for the query
    if len(search_tags) > MAX_QUERY_TAGS:
        raise HTTPException(
            status_code=400,
            detail=f"Number of tags for searching must not exceed {MAX_QUERY_TAGS}.",
        )

    placeholders = ",".join("?" for _ in search_tags)
    sql = f"""
        SELECT DISTINCT p.id, p.name
        FROM products p
        JOIN product_tags t ON p.id = t.product_id
        WHERE t.tag IN ({placeholders})
        ORDER BY p.id ASC
    """
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, search_tags)
        rows = cur.fetchall()
        return rows
    finally:
        conn.close()

def generate_html(product_rows: List[sqlite3.Row]) -> str:
    """
    Very simple HTML rendering of the product list with proper escaping.
    """
    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>Product Recommendations</title>",
        "</head>",
        "<body>",
        "<h1>Recommended Products</h1>",
    ]

    if not product_rows:
        html_parts.append("<p>No products match the provided tags.</p>")
    else:
        html_parts.append("<ul>")
        for row in product_rows:
            product_name = html.escape(row["name"])
            html_parts.append(f"<li>{product_name}</li>")
        html_parts.append("</ul>")

    html_parts.extend(["</body>", "</html>"])
    return "\n".join(html_parts)

# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.post(
    "/recommender",
    status_code=200,
    responses={400: {"description": "Invalid input"}},
)
def post_product(product: ProductCreate):
    """
    Post a new product along with its tags.
    """
    try:
        insert_product(product.product_name, product.tags)
    except Exception as exc:
        logger.exception("Failed to insert product.")
        raise HTTPException(status_code=400, detail=f"Database error: {exc}") from exc
    return {"detail": "Product created successfully"}

@app.get(
    "/recommender",
    response_class=HTMLResponse,
    summary="Get all posted products that match at least one of the provided tags.",
    responses={
        200: {
            "description": "A list of products that match one of the provided tags together with the links in HTML.",
            "content": {"text/html": {"schema": {"type": "string"}}},
        },
        400: {"description": "Invalid input"},
    },
)
def get_recommendations(
    tags: str = Query(..., description="Tags to search for separated by comma.")
):
    """
    Retrieve products that match any of the supplied tags.
    """
    # Split, clean, and enforce limits
    raw_tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
    if not raw_tags:
        raise HTTPException(
            status_code=400, detail="At least one non‑empty tag must be provided."
        )
    if len(raw_tags) > MAX_QUERY_TAGS:
        raise HTTPException(
            status_code=400,
            detail=f"The number of tags to search must not exceed {MAX_QUERY_TAGS}.",
        )
    for tag in raw_tags:
        if len(tag) > 30:
            raise HTTPException(
                status_code=400,
                detail="Each tag must be at most 30 characters long.",
            )

    product_rows = query_products_by_tags(raw_tags)
    html_content = generate_html(product_rows)
    return HTMLResponse(content=html_content, media_type="text/html")

# ---------------------------------------------------------------------------
# Run the application if executed directly
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)