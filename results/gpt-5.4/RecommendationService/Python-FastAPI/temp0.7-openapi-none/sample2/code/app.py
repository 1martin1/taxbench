import html
import os
import sqlite3
from contextlib import contextmanager
from typing import List

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

DB_PATH = "db.sqlite3"

app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS product_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_product_tags_tag
            ON product_tags(tag)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_product_tags_product_id
            ON product_tags(product_id)
            """
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


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
        seen = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("each tag must be a string")
            tag = item.strip()
            if not tag:
                raise ValueError("tags must not contain empty values")
            lowered = tag.lower()
            if lowered not in seen:
                seen.add(lowered)
                cleaned.append(tag)
        return cleaned


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 400:
        return JSONResponse(status_code=400, content={"detail": exc.detail})
    raise exc


@app.get(
    "/recommender",
    response_class=HTMLResponse,
    responses={
        200: {
            "description": "A list of products that match one of the provided tags together with the links in HTML.",
            "content": {"text/html": {"schema": {"type": "string"}}},
        }
    },
)
def get_recommender(tags: str = Query(..., description="Tags to search for separated by comma.")):
    parsed_tags = []
    seen = set()

    for raw_tag in tags.split(","):
        tag = raw_tag.strip()
        if tag:
            lowered = tag.lower()
            if lowered not in seen:
                seen.add(lowered)
                parsed_tags.append(lowered)

    if not parsed_tags:
        raise HTTPException(status_code=400, detail="At least one non-empty tag must be provided")

    placeholders = ",".join("?" for _ in parsed_tags)
    query = f"""
        SELECT DISTINCT p.id, p.product_name
        FROM products p
        INNER JOIN product_tags pt ON p.id = pt.product_id
        WHERE LOWER(pt.tag) IN ({placeholders})
        ORDER BY p.id ASC
    """

    with get_db() as conn:
        rows = conn.execute(query, parsed_tags).fetchall()

    parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        '<meta charset="utf-8">',
        "<title>Recommendations</title>",
        "</head>",
        "<body>",
        "<h1>Recommended Products</h1>",
    ]

    if rows:
        parts.append("<ul>")
        for row in rows:
            product_name = html.escape(row["product_name"])
            product_link = f"/products/{row['id']}"
            parts.append(
                f'<li><a href="{html.escape(product_link)}">{product_name}</a></li>'
            )
        parts.append("</ul>")
    else:
        parts.append("<p>No matching products found.</p>")

    parts.append("</body>")
    parts.append("</html>")

    return HTMLResponse(content="".join(parts), status_code=200)


@app.post(
    "/recommender",
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
    },
)
def post_recommender(payload: ProductCreate = Body(...)):
    try:
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO products (product_name) VALUES (?)",
                (payload.product_name,),
            )
            product_id = cursor.lastrowid

            conn.executemany(
                "INSERT INTO product_tags (product_id, tag) VALUES (?, ?)",
                [(product_id, tag) for tag in payload.tags],
            )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

    return {"status": "ok", "product_id": product_id}


@app.get("/products/{product_id}", response_class=HTMLResponse, include_in_schema=False)
def get_product(product_id: int):
    with get_db() as conn:
        product = conn.execute(
            "SELECT id, product_name FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()

        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")

        tags = conn.execute(
            "SELECT tag FROM product_tags WHERE product_id = ? ORDER BY id ASC",
            (product_id,),
        ).fetchall()

    parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{html.escape(product['product_name'])}</title>",
        "</head>",
        "<body>",
        f"<h1>{html.escape(product['product_name'])}</h1>",
        "<h2>Tags</h2>",
    ]

    if tags:
        parts.append("<ul>")
        for tag in tags:
            parts.append(f"<li>{html.escape(tag['tag'])}</li>")
        parts.append("</ul>")
    else:
        parts.append("<p>No tags.</p>")

    parts.append("</body>")
    parts.append("</html>")

    return HTMLResponse(content="".join(parts), status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)