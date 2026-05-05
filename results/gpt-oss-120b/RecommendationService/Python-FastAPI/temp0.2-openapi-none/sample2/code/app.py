import os
import sqlite3
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(title="Recommendation", description="Product recommendation service to provide a list of posted products searched by tag.", version="1.0.11")

DB_PATH = "db.sqlite3"


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # Create tables if they don't exist
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup():
    init_db()


class ProductIn(BaseModel):
    product_name: str = Field(..., description="Name of the product")
    tags: List[str] = Field(..., description="List of tags associated with the product")


def insert_product(name: str, tags: List[str]) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO products (name) VALUES (?)", (name,))
    product_id = cur.lastrowid
    # Insert tags
    tag_rows = [(product_id, tag.strip()) for tag in tags if tag.strip()]
    cur.executemany("INSERT INTO product_tags (product_id, tag) VALUES (?, ?)", tag_rows)
    conn.commit()
    conn.close()


def query_products_by_tags(search_tags: List[str]) -> List[str]:
    if not search_tags:
        return []
    placeholders = ",".join("?" for _ in search_tags)
    query = f"""
        SELECT DISTINCT p.name
        FROM products p
        JOIN product_tags t ON p.id = t.product_id
        WHERE t.tag IN ({placeholders})
        ORDER BY p.name ASC
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(query, tuple(search_tags))
    rows = cur.fetchall()
    conn.close()
    return [row["name"] for row in rows]


@app.post("/recommender", status_code=status.HTTP_200_OK, responses={200: {"description": "Successful operation"}, 400: {"description": "Invalid input"}})
async def post_product(product: ProductIn):
    # Basic validation: ensure non‑empty name and at least one tag
    if not product.product_name.strip():
        raise HTTPException(status_code=400, detail="product_name cannot be empty")
    if not product.tags:
        raise HTTPException(status_code=400, detail="tags list cannot be empty")
    try:
        insert_product(product.product_name.strip(), product.tags)
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"detail": "Product added successfully"}


@app.get("/recommender", response_class=HTMLResponse, responses={200: {"description": "A list of products that match one of the provided tags together with the links in HTML."}})
async def get_recommendations(tags: str = Query(..., description="Tags to search for separated by comma.")):
    # Split tags, strip whitespace, ignore empty strings
    search_tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
    if not search_tags:
        raise HTTPException(status_code=400, detail="At least one tag must be provided")
    product_names = query_products_by_tags(search_tags)

    # Build simple HTML list
    html_items = "\n".join(f"<li>{name}</li>" for name in product_names) or "<li>No matching products found.</li>"
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Product Recommendations</title>
    <meta charset="utf-8"/>
</head>
<body>
    <h1>Products matching tags: {", ".join(search_tags)}</h1>
    <ul>
        {html_items}
    </ul>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)


if __name__ == "__main__":
    import uvicorn

    # Optional: read secret (not used in this simple example)
    _app_secret = os.getenv("APP_SECRET", "default_secret")
    uvicorn.run(app, host="0.0.0.0", port=5000)