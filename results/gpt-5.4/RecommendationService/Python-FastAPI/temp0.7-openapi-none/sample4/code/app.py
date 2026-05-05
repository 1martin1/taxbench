import os
import html
import sqlite3
from contextlib import contextmanager
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


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
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
            "CREATE INDEX IF NOT EXISTS idx_product_tags_tag ON product_tags(tag)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_tags_product_id ON product_tags(product_id)"
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
            raise ValueError("tags must be a non-empty array")
        cleaned = []
        seen = set()
        for tag in value:
            if not isinstance(tag, str):
                raise ValueError("each tag must be a string")
            normalized = tag.strip()
            if not normalized:
                raise ValueError("tags must not contain empty values")
            lowered = normalized.lower()
            if lowered not in seen:
                seen.add(lowered)
                cleaned.append(lowered)
        if not cleaned:
            raise ValueError("tags must contain at least one valid tag")
        return cleaned


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 400:
        return JSONResponse(status_code=400, content={"detail": exc.detail})
    raise exc


@app.get("/recommender", response_class=HTMLResponse, summary="Get all posted products that match at least one of the provided tags.")
def get_recommender(
    tags: str = Query(
        ...,
        description="Tags to search for separated by comma.",
    )
) -> HTMLResponse:
    parsed_tags = []
    seen = set()
    for tag in tags.split(","):
        normalized = tag.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            parsed_tags.append(normalized)

    if not parsed_tags:
        raise HTTPException(status_code=400, detail="tags query parameter must contain at least one tag")

    placeholders = ",".join("?" for _ in parsed_tags)
    query = f"""
        SELECT DISTINCT p.id, p.product_name
        FROM products p
        INNER JOIN product_tags pt ON pt.product_id = p.id
        WHERE pt.tag IN ({placeholders})
        ORDER BY p.id ASC
    """

    with get_db() as conn:
        rows = conn.execute(query, parsed_tags).fetchall()

    items = []
    for row in rows:
        product_name = html.escape(row["product_name"])
        product_link = f"/products/{row['id']}"
        items.append(f'<li><a href="{html.escape(product_link)}">{product_name}</a></li>')

    if items:
        body = "<ul>" + "".join(items) + "</ul>"
    else:
        body = "<p>No matching products found.</p>"

    return HTMLResponse(content=body, status_code=200, media_type="text/html")


@app.post("/recommender", summary="Post a new product along with its tags.")
def post_recommender(payload: ProductCreate):
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

    return {"status": "success", "product_id": product_id}


@app.get("/products/{product_id}", response_class=HTMLResponse, include_in_schema=False)
def get_product_page(product_id: int) -> HTMLResponse:
    with get_db() as conn:
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

    safe_name = html.escape(product["product_name"])
    tag_list = "".join(f"<li>{html.escape(tag['tag'])}</li>" for tag in tags)
    content = f"""
    <html>
        <head>
            <title>{safe_name}</title>
        </head>
        <body>
            <h1>{safe_name}</h1>
            <h2>Tags</h2>
            <ul>{tag_list}</ul>
        </body>
    </html>
    """
    return HTMLResponse(content=content, status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)