import os
import sqlite3
import html
from typing import List

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager

DB_PATH = "db.sqlite3"


def get_db_connection() -> sqlite3.Connection:
    """Create a new SQLite connection with row factory for dict-like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the SQLite database with required tables."""
    conn = get_db_connection()
    try:
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
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler to set up resources."""
    init_db()
    yield
    # No teardown needed for this simple app


app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
    lifespan=lifespan,
)


class ProductIn(BaseModel):
    product_name: str = Field(..., min_length=1, description="Name of the product")
    tags: List[str] = Field(..., min_items=1, description="List of tags for the product")


@app.post(
    "/recommender",
    status_code=status.HTTP_200_OK,
    responses={400: {"description": "Invalid input"}, 500: {"description": "Database error"}},
)
def add_product(product: ProductIn):
    # Validate product name
    name = product.product_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="product_name cannot be empty")

    # Clean tags
    cleaned_tags = [t.strip() for t in product.tags if t.strip()]
    if not cleaned_tags:
        raise HTTPException(status_code=400, detail="At least one non‑empty tag is required")

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO products (name) VALUES (?)", (name,))
        product_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO tags (product_id, tag) VALUES (?, ?)",
            [(product_id, tag) for tag in cleaned_tags],
        )
        conn.commit()
    except sqlite3.Error as e:
        # Log the error in a real application
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        conn.close()

    return {"message": "Product added successfully", "product_id": product_id}


@app.get(
    "/recommender",
    response_class=HTMLResponse,
    summary="Get all posted products that match at least one of the provided tags.",
    responses={400: {"description": "Invalid input"}, 500: {"description": "Database error"}},
)
def get_products(tags: str = Query(..., description="Tags to search for separated by comma.")):
    # Parse and clean tags from query string
    search_tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not search_tags:
        raise HTTPException(status_code=400, detail="At least one tag must be provided")

    placeholders = ",".join("?" for _ in search_tags)

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Retrieve distinct products that have any of the requested tags
        cur.execute(
            f"""
            SELECT DISTINCT p.id, p.name
            FROM products p
            JOIN tags t ON p.id = t.product_id
            WHERE t.tag IN ({placeholders})
            ORDER BY p.id
            """,
            tuple(search_tags),
        )
        products = cur.fetchall()

        # Gather tags for each product
        product_list = []
        for prod in products:
            cur.execute("SELECT tag FROM tags WHERE product_id = ?", (prod["id"],))
            tags_rows = cur.fetchall()
            prod_tags = [row["tag"] for row in tags_rows]
            product_list.append(
                {"id": prod["id"], "name": prod["name"], "tags": prod_tags}
            )
    except sqlite3.Error as e:
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
        "<h1>Products matching tags</h1>",
    ]

    if not product_list:
        html_parts.append("<p>No products found for the given tags.</p>")
    else:
        html_parts.append("<ul>")
        for p in product_list:
            escaped_name = html.escape(p["name"])
            escaped_tags = ", ".join(html.escape(tag) for tag in p["tags"])
            html_parts.append(
                f"<li><strong>{escaped_name}</strong> (tags: {escaped_tags})</li>"
            )
        html_parts.append("</ul>")

    html_parts.extend(["</body>", "</html>"])
    html_content = "\n".join(html_parts)

    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    import uvicorn

    # Optional: read secret (not used in this simple example)
    _ = os.getenv("APP_SECRET", "")
    uvicorn.run(app, host="0.0.0.0", port=5000)