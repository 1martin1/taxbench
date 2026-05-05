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
    conn.close()


@app.on_event("startup")
def on_startup():
    init_db()


class ProductIn(BaseModel):
    product_name: str = Field(..., min_length=1, description="Name of the product")
    tags: List[str] = Field(..., min_items=1, description="List of tags for the product")


@app.post("/recommender", status_code=status.HTTP_200_OK, responses={400: {"description": "Invalid input"}})
def add_product(product: ProductIn):
    # Basic validation (Pydantic already does most)
    if not product.product_name.strip():
        raise HTTPException(status_code=400, detail="product_name cannot be empty")
    cleaned_tags = [t.strip() for t in product.tags if t.strip()]
    if not cleaned_tags:
        raise HTTPException(status_code=400, detail="At least one non‑empty tag is required")

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO products (name) VALUES (?)", (product.product_name.strip(),))
        product_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO tags (product_id, tag) VALUES (?, ?)",
            [(product_id, tag) for tag in cleaned_tags],
        )
        conn.commit()
    finally:
        conn.close()
    return {"message": "Product added successfully", "product_id": product_id}


@app.get("/recommender", response_class=HTMLResponse, summary="Get all posted products that match at least one of the provided tags.")
def get_products(tags: str = Query(..., description="Tags to search for separated by comma.")):
    # Parse tags
    search_tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not search_tags:
        raise HTTPException(status_code=400, detail="At least one tag must be provided")

    placeholders = ",".join("?" for _ in search_tags)

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Get distinct products that have any of the tags
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

        # For each product, fetch its tags
        product_list = []
        for prod in products:
            cur.execute(
                "SELECT tag FROM tags WHERE product_id = ?", (prod["id"],)
            )
            tags_rows = cur.fetchall()
            prod_tags = [row["tag"] for row in tags_rows]
            product_list.append({"id": prod["id"], "name": prod["name"], "tags": prod_tags})
    finally:
        conn.close()

    # Build simple HTML
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
            tags_str = ", ".join(p["tags"])
            html_parts.append(f"<li><strong>{p['name']}</strong> (tags: {tags_str})</li>")
        html_parts.append("</ul>")

    html_parts.extend(["</body>", "</html>"])
    html_content = "\n".join(html_parts)
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    import uvicorn

    # Optional: read secret (not used in this simple example)
    _ = os.getenv("APP_SECRET", "")
    uvicorn.run(app, host="0.0.0.0", port=5000)