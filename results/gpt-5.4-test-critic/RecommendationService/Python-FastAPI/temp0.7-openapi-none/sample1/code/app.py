import html
import os
import sqlite3
from contextlib import contextmanager
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError


DB_PATH = "db.sqlite3"


app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)


class ProductCreate(BaseModel):
    product_name: str = Field(..., min_length=1)
    tags: List[str]


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
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_tags_tag ON product_tags(tag)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_tags_product_id ON product_tags(product_id)"
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


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/recommender", response_class=HTMLResponse)
def get_recommendations(tags: str = Query(..., description="Tags to search for separated by comma.")):
    parsed_tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
    if not parsed_tags:
        raise HTTPException(status_code=400, detail="At least one non-empty tag must be provided.")

    normalized_tags = list(dict.fromkeys(tag.lower() for tag in parsed_tags))

    placeholders = ",".join("?" for _ in normalized_tags)
    query = f"""
        SELECT DISTINCT p.id, p.product_name
        FROM products p
        JOIN product_tags pt ON p.id = pt.product_id
        WHERE LOWER(pt.tag) IN ({placeholders})
        ORDER BY p.id ASC
    """

    with get_db() as conn:
        rows = conn.execute(query, normalized_tags).fetchall()

    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        '<meta charset="utf-8">',
        "<title>Recommendations</title>",
        "</head>",
        "<body>",
        "<h1>Recommended Products</h1>",
    ]

    if not rows:
        html_parts.append("<p>No matching products found.</p>")
    else:
        html_parts.append("<ul>")
        for row in rows:
            product_id = row["id"]
            product_name = html.escape(row["product_name"])
            html_parts.append(
                f'<li><a href="/recommender?tags={html.escape(tags, quote=True)}#product-{product_id}" id="product-{product_id}">{product_name}</a></li>'
            )
        html_parts.append("</ul>")

    html_parts.extend(["</body>", "</html>"])
    return HTMLResponse(content="".join(html_parts), status_code=200)


@app.post("/recommender")
def post_product(payload: ProductCreate):
    product_name = payload.product_name.strip()
    if not product_name:
        return JSONResponse(status_code=400, content={"detail": "product_name must not be empty."})

    if not isinstance(payload.tags, list) or len(payload.tags) == 0:
        return JSONResponse(status_code=400, content={"detail": "tags must be a non-empty array of strings."})

    cleaned_tags = []
    for tag in payload.tags:
        if not isinstance(tag, str):
            return JSONResponse(status_code=400, content={"detail": "Each tag must be a string."})
        cleaned = tag.strip()
        if not cleaned:
            return JSONResponse(status_code=400, content={"detail": "Tags must not be empty."})
        cleaned_tags.append(cleaned)

    deduplicated_tags = list(dict.fromkeys(cleaned_tags))

    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO products (product_name) VALUES (?)",
            (product_name,),
        )
        product_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO product_tags (product_id, tag) VALUES (?, ?)",
            [(product_id, tag) for tag in deduplicated_tags],
        )

    return {"status": "success", "product_id": product_id}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)