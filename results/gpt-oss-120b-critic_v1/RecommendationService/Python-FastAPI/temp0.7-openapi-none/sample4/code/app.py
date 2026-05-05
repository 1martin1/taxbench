import os
import html
import sqlite3
from typing import List

from fastapi import FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, validator

# ---------- Configuration ----------
DB_PATH = "db.sqlite3"
MAX_TAGS_QUERY = 50          # Maximum number of tags allowed in GET request
MAX_TAGS_POST = 50           # Maximum number of tags allowed in POST request
MAX_TAG_LENGTH = 30          # Maximum characters per tag
MAX_PRODUCT_NAME_LENGTH = 100  # Maximum characters for product name

# ---------- Database Setup ----------
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = get_connection()
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
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_product_tags_tag ON product_tags(tag)")
    conn.commit()
    conn.close()

# ---------- Pydantic Models ----------
class ProductCreate(BaseModel):
    product_name: str = Field(
        ...,
        min_length=1,
        max_length=MAX_PRODUCT_NAME_LENGTH,
        description="Name of the product"
    )
    tags: List[str] = Field(
        ...,
        min_items=1,
        max_items=MAX_TAGS_POST,
        description="List of tags for the product"
    )

    @validator("product_name")
    def strip_product_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Product name cannot be empty or whitespace")
        return v

    @validator("tags", each_item=True)
    def validate_tag(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Tag cannot be empty or whitespace")
        if len(v) > MAX_TAG_LENGTH:
            raise ValueError(f"Tag exceeds maximum length of {MAX_TAG_LENGTH}")
        return v

# ---------- FastAPI App ----------
app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11"
)

@app.on_event("startup")
def on_startup() -> None:
    init_db()

# ---------- POST /recommender ----------
@app.post(
    "/recommender",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
        500: {"description": "Database error"},
    },
)
def add_product(product: ProductCreate):
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Insert product
        cur.execute(
            "INSERT INTO products (name) VALUES (?)",
            (product.product_name,)
        )
        product_id = cur.lastrowid

        # Insert tags
        tag_rows = [(product_id, tag) for tag in product.tags]
        cur.executemany(
            "INSERT INTO product_tags (product_id, tag) VALUES (?, ?)",
            tag_rows
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        conn.close()
    return {"status": "ok"}

# ---------- GET /recommender ----------
@app.get(
    "/recommender",
    response_class=Response,
    responses={
        200: {
            "description": "A list of products that match one of the provided tags together with the links in HTML.",
            "content": {"text/html": {"schema": {"type": "string"}}},
        },
        400: {"description": "Invalid input"},
        500: {"description": "Database error"},
    },
)
def get_recommendations(
    tags: str = Query(..., description="Tags to search for separated by comma.")
):
    # Parse and clean tags
    raw_tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not raw_tags:
        raise HTTPException(status_code=400, detail="At least one tag must be provided.")
    if len(raw_tags) > MAX_TAGS_QUERY:
        raise HTTPException(
            status_code=400,
            detail=f"Number of tags exceeds maximum allowed ({MAX_TAGS_QUERY}).",
        )

    placeholders = ",".join("?" for _ in raw_tags)
    query = f"""
        SELECT p.id, p.name, GROUP_CONCAT(pt.tag, ', ') AS tags
        FROM products p
        JOIN product_tags pt ON p.id = pt.product_id
        WHERE pt.tag IN ({placeholders})
        GROUP BY p.id
        ORDER BY p.id ASC
    """

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, raw_tags)
        rows = cur.fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        conn.close()

    # Build safe HTML response
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

    if rows:
        html_parts.append("<ul>")
        for row in rows:
            product_name = html.escape(row["name"])
            product_tags = html.escape(row["tags"] or "")
            html_parts.append(f"<li>{product_name} (tags: {product_tags})</li>")
        html_parts.append("</ul>")
    else:
        html_parts.append("<p>No products match the given tags.</p>")

    html_parts.extend(["</body>", "</html>"])
    html_content = "\n".join(html_parts)

    return Response(content=html_content, media_type="text/html")

# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, log_level="info")