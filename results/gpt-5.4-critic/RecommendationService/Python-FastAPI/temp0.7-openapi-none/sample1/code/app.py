import html
import os
import sqlite3
from contextlib import contextmanager
from typing import List

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field, ValidationError

DB_PATH = "db.sqlite3"

MAX_PRODUCT_NAME_LENGTH = 255
MAX_TAG_LENGTH = 64
MAX_POST_TAGS = 50
MAX_GET_TAGS = 20
MAX_RESULTS = 100

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
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class ProductCreate(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=MAX_PRODUCT_NAME_LENGTH)
    tags: List[str]

    class Config:
        extra = "forbid"


def validate_product_payload(payload: ProductCreate) -> ProductCreate:
    if not isinstance(payload.product_name, str):
        raise HTTPException(status_code=400, detail="Invalid input")

    cleaned_name = payload.product_name.strip()
    if not cleaned_name or len(cleaned_name) > MAX_PRODUCT_NAME_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    if not isinstance(payload.tags, list) or len(payload.tags) == 0 or len(payload.tags) > MAX_POST_TAGS:
        raise HTTPException(status_code=400, detail="Invalid input")

    cleaned_tags = []
    for tag in payload.tags:
        if not isinstance(tag, str):
            raise HTTPException(status_code=400, detail="Invalid input")
        cleaned_tag = tag.strip()
        if not cleaned_tag or len(cleaned_tag) > MAX_TAG_LENGTH:
            raise HTTPException(status_code=400, detail="Invalid input")
        cleaned_tags.append(cleaned_tag)

    payload.product_name = cleaned_name
    payload.tags = cleaned_tags
    return payload


def parse_query_tags(tags: str) -> List[str]:
    if len(tags) > (MAX_GET_TAGS * (MAX_TAG_LENGTH + 1)):
        return []

    parsed = []
    for raw_tag in tags.split(","):
        cleaned = raw_tag.strip()
        if cleaned:
            if len(cleaned) > MAX_TAG_LENGTH:
                continue
            parsed.append(cleaned)
            if len(parsed) >= MAX_GET_TAGS:
                break
    return parsed


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get(
    "/recommender",
    response_class=Response,
    responses={
        200: {
            "description": "A list of products that match one of the provided tags together with the links in HTML.",
            "content": {"text/html": {"schema": {"type": "string"}}},
        }
    },
)
def get_recommendations(
    tags: str = Query(..., description="Tags to search for separated by comma.")
):
    parsed_tags = parse_query_tags(tags)

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

    if not parsed_tags:
        html_parts.append("<p>No matching products found.</p>")
        html_parts.extend(["</body>", "</html>"])
        return Response(content="".join(html_parts), media_type="text/html")

    placeholders = ",".join("?" for _ in parsed_tags)
    query = f"""
        SELECT DISTINCT p.id, p.product_name
        FROM products p
        INNER JOIN product_tags pt ON pt.product_id = p.id
        WHERE pt.tag IN ({placeholders})
        ORDER BY p.id ASC
        LIMIT ?
    """

    try:
        with get_db() as conn:
            rows = conn.execute(query, parsed_tags + [MAX_RESULTS]).fetchall()

            product_ids = [row["id"] for row in rows]
            tags_by_product = {}

            if product_ids:
                id_placeholders = ",".join("?" for _ in product_ids)
                tag_rows = conn.execute(
                    f"""
                    SELECT pt.product_id, pt.tag
                    FROM product_tags pt
                    WHERE pt.product_id IN ({id_placeholders})
                    ORDER BY pt.product_id ASC, pt.tag ASC
                    """,
                    product_ids,
                ).fetchall()

                for row in tag_rows:
                    tags_by_product.setdefault(row["product_id"], []).append(row["tag"])
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error")

    if not rows:
        html_parts.append("<p>No matching products found.</p>")
    else:
        html_parts.append("<ul>")
        for row in rows:
            product_name = html.escape(row["product_name"])
            product_tags = ", ".join(
                html.escape(tag) for tag in tags_by_product.get(row["id"], [])
            )
            html_parts.append(
                f"<li>{product_name} - Tags: {product_tags}</li>"
            )
        html_parts.append("</ul>")

    html_parts.extend(["</body>", "</html>"])
    return Response(content="".join(html_parts), media_type="text/html")


@app.post(
    "/recommender",
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
    },
)
def create_product(payload: ProductCreate = Body(...)):
    try:
        payload = validate_product_payload(payload)
        unique_tags = list(dict.fromkeys(payload.tags))

        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO products (product_name) VALUES (?)",
                (payload.product_name,),
            )
            product_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO product_tags (product_id, tag) VALUES (?, ?)",
                [(product_id, tag) for tag in unique_tags],
            )
    except (ValidationError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid input")
    except HTTPException:
        raise
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid input")
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error")

    return Response(status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)