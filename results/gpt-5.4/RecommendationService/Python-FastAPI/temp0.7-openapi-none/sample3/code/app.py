import html
import os
import sqlite3
from contextlib import closing
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

DB_PATH = "db.sqlite3"

app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)


class ProductCreate(BaseModel):
    product_name: str = Field(..., min_length=1)
    tags: List[str]

    @field_validator("product_name")
    @classmethod
    def validate_product_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("product_name must not be empty")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: List[str]) -> List[str]:
        if not isinstance(value, list) or len(value) == 0:
            raise ValueError("tags must be a non-empty array of strings")
        cleaned = []
        for tag in value:
            if not isinstance(tag, str):
                raise ValueError("each tag must be a string")
            normalized = tag.strip().lower()
            if not normalized:
                raise ValueError("tags must not contain empty values")
            cleaned.append(normalized)
        return cleaned


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(get_connection()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS product_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_product_tags_tag ON product_tags(tag);
            CREATE INDEX IF NOT EXISTS idx_product_tags_product_id ON product_tags(product_id);
            """
        )
        conn.commit()


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 400:
        return JSONResponse(status_code=400, content={"detail": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.post("/recommender")
def post_product(payload: ProductCreate):
    product_name = payload.product_name.strip()
    tags = list(dict.fromkeys(payload.tags))

    try:
        with closing(get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO products (product_name) VALUES (?)",
                (product_name,),
            )
            product_id = cursor.lastrowid
            cursor.executemany(
                "INSERT INTO product_tags (product_id, tag) VALUES (?, ?)",
                [(product_id, tag) for tag in tags],
            )
            conn.commit()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

    return {"status": "ok", "product_id": product_id}


@app.get("/recommender", response_class=HTMLResponse)
def get_recommendations(tags: str = Query(..., description="Tags to search for separated by comma.")):
    parsed_tags = []
    for tag in tags.split(","):
        normalized = tag.strip().lower()
        if normalized:
            parsed_tags.append(normalized)

    if not parsed_tags:
        raise HTTPException(status_code=400, detail="At least one valid tag must be provided")

    placeholders = ",".join("?" for _ in parsed_tags)
    query = f"""
        SELECT DISTINCT p.id, p.product_name
        FROM products p
        INNER JOIN product_tags pt ON p.id = pt.product_id
        WHERE pt.tag IN ({placeholders})
        ORDER BY p.id ASC
    """

    with closing(get_connection()) as conn:
        rows = conn.execute(query, parsed_tags).fetchall()

    body_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        '<meta charset="utf-8">',
        "<title>Recommendation Results</title>",
        "</head>",
        "<body>",
        "<h1>Recommended Products</h1>",
    ]

    if rows:
        body_parts.append("<ul>")
        for row in rows:
            product_name = html.escape(row["product_name"])
            product_link = f"/products/{row['id']}"
            body_parts.append(f'<li><a href="{html.escape(product_link)}">{product_name}</a></li>')
        body_parts.append("</ul>")
    else:
        body_parts.append("<p>No matching products found.</p>")

    body_parts.append("</body>")
    body_parts.append("</html>")

    return HTMLResponse(content="".join(body_parts), status_code=200)


@app.get("/products/{product_id}", response_class=HTMLResponse)
def get_product_page(product_id: int):
    with closing(get_connection()) as conn:
        product = conn.execute(
            "SELECT id, product_name FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")

        tags = conn.execute(
            "SELECT tag FROM product_tags WHERE product_id = ? ORDER BY tag ASC",
            (product_id,),
        ).fetchall()

    tag_list = ", ".join(html.escape(row["tag"]) for row in tags)

    content = (
        "<!DOCTYPE html>"
        "<html>"
        "<head><meta charset='utf-8'><title>Product</title></head>"
        "<body>"
        f"<h1>{html.escape(product['product_name'])}</h1>"
        f"<p><strong>ID:</strong> {product['id']}</p>"
        f"<p><strong>Tags:</strong> {tag_list}</p>"
        "</body>"
        "</html>"
    )
    return HTMLResponse(content=content, status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)