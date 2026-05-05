import os
import sqlite3
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError, validator
from jinja2 import Template

# ---------- Database utilities ----------
DB_PATH = "db.sqlite3"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        cur = conn.cursor()
        # Create products table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )
        """)
        # Create tags table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            )
        """)
        conn.commit()

# ---------- Pydantic models ----------
class ProductIn(BaseModel):
    product_name: str = Field(..., min_length=1)
    tags: List[str] = Field(..., min_items=1)

    @validator('tags', each_item=True)
    def non_empty_tag(cls, v):
        if not isinstance(v, str) or not v.strip():
            raise ValueError('each tag must be a non‑empty string')
        return v.strip()

# ---------- FastAPI app ----------
app = FastAPI(title="Recommendation", description="Product recommendation service to provide a list of posted products searched by tag.", version="1.0.11")

@app.on_event("startup")
def on_startup():
    init_db()

# ---------- HTML template ----------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Product Recommendations</title>
    <style>
        body {font-family: Arial, sans-serif; margin: 2rem;}
        ul {list-style-type: none; padding: 0;}
        li {margin: 0.5rem 0;}
        .product {font-weight: bold;}
        .tags {color: #555;}
    </style>
</head>
<body>
    <h1>Product Recommendations</h1>
    {% if products %}
        <ul>
        {% for p in products %}
            <li>
                <span class="product">{{ p.name }}</span>
                {% if p.tags %}
                    <span class="tags">[{{ p.tags|join(', ') }}]</span>
                {% endif %}
            </li>
        {% endfor %}
        </ul>
    {% else %}
        <p>No products match the given tags.</p>
    {% endif %}
</body>
</html>
"""

template = Template(HTML_TEMPLATE)

# ---------- Endpoints ----------
@app.post("/recommender", status_code=status.HTTP_200_OK)
def add_product(product: ProductIn):
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            # Insert product
            cur.execute("INSERT INTO products (name) VALUES (?)", (product.product_name.strip(),))
            product_id = cur.lastrowid
            # Insert tags
            tag_rows = [(product_id, tag) for tag in product.tags]
            cur.executemany("INSERT INTO tags (product_id, tag) VALUES (?, ?)", tag_rows)
            conn.commit()
        return {"message": "Product added successfully", "product_id": product_id}
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/recommender", response_class=HTMLResponse)
def get_products(tags: str = Query(..., description="Tags to search for separated by comma.")):
    # Parse tags
    search_tags = [t.strip().lower() for t in tags.split(",") if t.strip()]
    if not search_tags:
        raise HTTPException(status_code=400, detail="At least one tag must be provided.")
    placeholders = ",".join("?" for _ in search_tags)

    query = f"""
        SELECT DISTINCT p.id, p.name
        FROM products p
        JOIN tags t ON p.id = t.product_id
        WHERE LOWER(t.tag) IN ({placeholders})
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, tuple(search_tags))
        product_rows = cur.fetchall()

        products = []
        for row in product_rows:
            # Get tags for each product
            cur.execute("SELECT tag FROM tags WHERE product_id = ?", (row["id"],))
            tags_list = [r["tag"] for r in cur.fetchall()]
            products.append({"name": row["name"], "tags": tags_list})

    rendered_html = template.render(products=products)
    return HTMLResponse(content=rendered_html, status_code=200)

# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    # Optional: read secret (not used in this simple example)
    APP_SECRET = os.getenv("APP_SECRET", "default_secret")

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)