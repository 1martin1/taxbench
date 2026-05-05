import os
import sqlite3
import html
import urllib.parse
from typing import List, Generator

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)

DB_PATH = "db.sqlite3"


def get_connection() -> sqlite3.Connection:
    """Create a SQLite connection with row factory for dict‑like access."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they do not exist."""
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
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def db_dependency() -> Generator[sqlite3.Connection, None, None]:
    """Provide a DB connection per request."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


class ProductIn(BaseModel):
    product_name: str = Field(..., description="Name of the product")
    tags: List[str] = Field(..., description="List of tags for the product")


@app.post(
    "/recommender",
    status_code=200,
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
    },
)
def create_product(
    product: ProductIn, db: sqlite3.Connection = Depends(db_dependency)
):
    # Basic validation
    if not product.product_name.strip():
        raise HTTPException(status_code=400, detail="product_name cannot be empty")
    if not product.tags:
        raise HTTPException(status_code=400, detail="tags list cannot be empty")

    cur = db.cursor()
    cur.execute(
        "INSERT INTO products (name) VALUES (?)", (product.product_name.strip(),)
    )
    product_id = cur.lastrowid

    tag_rows = [
        (product_id, tag.strip())
        for tag in product.tags
        if tag.strip()
    ]
    if not tag_rows:
        raise HTTPException(status_code=400, detail="All tags are empty")
    cur.executemany(
        "INSERT INTO product_tags (product_id, tag) VALUES (?, ?)", tag_rows
    )
    db.commit()
    return {"message": "Product created successfully", "product_id": product_id}


@app.get(
    "/recommender",
    response_class=HTMLResponse,
    responses={
        200: {
            "description": "A list of products that match one of the provided tags together with the links in HTML."
        }
    },
)
def get_products_by_tags(
    tags: str = Query(..., description="Tags to search for separated by comma."),
    db: sqlite3.Connection = Depends(db_dependency),
):
    # Parse tags
    search_tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not search_tags:
        raise HTTPException(
            status_code=400, detail="At least one non-empty tag must be provided"
        )

    placeholders = ",".join("?" for _ in search_tags)
    query = f"""
        SELECT DISTINCT p.id, p.name
        FROM products p
        JOIN product_tags pt ON p.id = pt.product_id
        WHERE pt.tag IN ({placeholders})
        ORDER BY p.id ASC
    """
    cur = db.cursor()
    cur.execute(query, tuple(search_tags))
    rows = cur.fetchall()

    html_parts = [
        "<html>",
        "<head><title>Product Recommendations</title></head>",
        "<body>",
        "<h1>Recommended Products</h1>",
    ]

    if rows:
        html_parts.append("<ul>")
        for row in rows:
            product_id = row["id"]
            product_name = html.escape(row["name"])
            html_parts.append(
                f'<li><a href="/product/{product_id}">{product_name}</a></li>'
            )
        html_parts.append("</ul>")
    else:
        html_parts.append("<p>No products found for the given tags.</p>")

    html_parts.extend(["</body>", "</html>"])
    html_content = "\n".join(html_parts)
    return HTMLResponse(content=html_content, status_code=200)


@app.get("/product/{product_id}", response_class=HTMLResponse)
def product_detail(
    product_id: int, db: sqlite3.Connection = Depends(db_dependency)
):
    cur = db.cursor()
    cur.execute("SELECT name FROM products WHERE id = ?", (product_id,))
    prod = cur.fetchone()
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found")

    cur.execute(
        "SELECT tag FROM product_tags WHERE product_id = ?", (product_id,)
    )
    tags = [r["tag"] for r in cur.fetchall()]

    escaped_name = html.escape(prod["name"])
    escaped_tags_display = ", ".join(html.escape(tag) for tag in tags) if tags else "None"

    # Build URL‑encoded tag list for the similarity link
    if tags:
        encoded_tags = urllib.parse.quote_plus(",".join(tags))
        similar_link = f'/recommender?tags={encoded_tags}'
        similar_anchor = f'<p><a href="{similar_link}">See similar products</a></p>'
    else:
        similar_anchor = ""

    html_content = f"""
    <html>
    <head><title>{escaped_name}</title></head>
    <body>
        <h1>{escaped_name}</h1>
        <p><strong>Tags:</strong> {escaped_tags_display}</p>
        {similar_anchor}
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)


if __name__ == "__main__":
    import uvicorn

    # Read APP_SECRET to satisfy the requirement (not used further)
    _ = os.getenv("APP_SECRET")
    uvicorn.run(app, host="0.0.0.0", port=5000)