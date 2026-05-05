import os
import sqlite3
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

app = FastAPI(title="Recommendation", description="Product recommendation service to provide a list of posted products searched by tag.", version="1.0.11")

DB_PATH = "db.sqlite3"

def get_db_connection():
    # Using check_same_thread=False to allow usage across FastAPI threads
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # Create tables if they don't exist
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS product_tags (
            product_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

class ProductCreate(BaseModel):
    product_name: str = Field(..., description="Name of the product")
    tags: List[str] = Field(..., description="List of tags associated with the product")

    @classmethod
    def validate_tags(cls, v):
        if not isinstance(v, list):
            raise ValueError("tags must be a list")
        for item in v:
            if not isinstance(item, str):
                raise ValueError("each tag must be a string")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "product_name": "Super Widget",
                "tags": ["gadget", "tool", "sale"]
            }
        }

@app.post("/recommender", status_code=status.HTTP_200_OK, responses={
    200: {"description": "Successful operation"},
    400: {"description": "Invalid input"}
})
def create_product(product: ProductCreate):
    # Basic validation already performed by Pydantic
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO products (product_name) VALUES (?)", (product.product_name,))
        product_id = cur.lastrowid
        # Insert tags
        tag_rows = [(product_id, tag.strip()) for tag in product.tags if tag.strip()]
        cur.executemany("INSERT INTO product_tags (product_id, tag) VALUES (?, ?)", tag_rows)
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        conn.close()
    return {"message": "Product created successfully", "product_id": product_id}

@app.get("/recommender", response_class=HTMLResponse, responses={
    200: {
        "description": "A list of products that match one of the provided tags together with the links in HTML.",
        "content": {"text/html": {"schema": {"type": "string"}}}
    }
})
def get_recommendations(tags: str = Query(..., description="Tags to search for separated by comma.")):
    # Parse tags
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()]
    if not tag_list:
        raise HTTPException(status_code=400, detail="At least one tag must be provided")
    placeholders = ",".join("?" for _ in tag_list)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        query = f"""
            SELECT DISTINCT p.id, p.product_name
            FROM products p
            JOIN product_tags t ON p.id = t.product_id
            WHERE t.tag IN ({placeholders})
            ORDER BY p.product_name ASC
        """
        cur.execute(query, tag_list)
        rows = cur.fetchall()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        conn.close()

    # Build HTML response
    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>Product Recommendations</title>",
        "</head>",
        "<body>",
        f"<h1>Products matching tags: {', '.join(tag_list)}</h1>"
    ]

    if rows:
        html_parts.append("<ul>")
        for row in rows:
            product_name = row["product_name"]
            product_id = row["id"]
            # Assuming a placeholder link; in real scenario could link to product page
            html_parts.append(f"<li>{product_name} (ID: {product_id})</li>")
        html_parts.append("</ul>")
    else:
        html_parts.append("<p>No products found for the given tags.</p>")

    html_parts.extend(["</body>", "</html>"])
    html_content = "\n".join(html_parts)
    return HTMLResponse(content=html_content, status_code=200)

if __name__ == "__main__":
    import uvicorn
    # Bind to 0.0.0.0 on port 5000 as required
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)