import os
import sqlite3
import html
from typing import List

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, validator

app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)

DB_PATH = "db.sqlite3"


def get_db_connection() -> sqlite3.Connection:
    """
    Returns a new SQLite connection with row factory set to return dict‑like rows.
    check_same_thread=False allows usage across FastAPI's async workers.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the required tables if they do not already exist."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS product_tags (
                product_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


class ProductCreate(BaseModel):
    product_name: str = Field(..., description="Name of the product")
    tags: List[str] = Field(..., description="List of tags associated with the product")

    @validator("product_name")
    def strip_product_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("product_name cannot be empty")
        return v

    @validator("tags", each_item=True)
    def strip_and_validate_tag(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("tags cannot contain empty strings")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "product_name": "Super Widget",
                "tags": ["gadget", "tool", "sale"],
            }
        }


@app.post(
    "/recommender",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
        500: {"description": "Internal server error"},
    },
)
def create_product(product: ProductCreate):
    """
    Store a new product and its associated tags.
    """
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO products (product_name) VALUES (?)",
                (product.product_name,),
            )
            product_id = cur.lastrowid

            tag_rows = [(product_id, tag) for tag in product.tags]
            if tag_rows:
                cur.executemany(
                    "INSERT INTO product_tags (product_id, tag) VALUES (?, ?)",
                    tag_rows,
                )
            conn.commit()
    except sqlite3.Error as e:
        # Log the error in a real application
        raise HTTPException(status_code=500, detail="Database error") from e

    return {"message": "Product created successfully", "product_id": product_id}


@app.get(
    "/recommender",
    response_class=HTMLResponse,
    responses={
        200: {
            "description": "A list of products that match one of the provided tags together with the links in HTML.",
            "content": {"text/html": {"schema": {"type": "string"}}},
        },
        400: {"description": "Bad request"},
        500: {"description": "Internal server error"},
    },
)
def get_recommendations(tags: str = Query(..., description="Tags to search for separated by comma.")):
    """
    Return an HTML page listing products that have at least one of the supplied tags.
    """
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()]
    if not tag_list:
        raise HTTPException(status_code=400, detail="At least one tag must be provided")

    placeholders = ",".join("?" for _ in tag_list)

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
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
        raise HTTPException(status_code=500, detail="Database error") from e

    # Build a safe HTML response
    escaped_tags = ", ".join(html.escape(tag) for tag in tag_list)
    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>Product Recommendations</title>",
        "</head>",
        "<body>",
        f"<h1>Products matching tags: {escaped_tags}</h1>",
    ]

    if rows:
        html_parts.append("<ul>")
        for row in rows:
            product_name = html.escape(row["product_name"])
            product_id = row["id"]
            html_parts.append(f"<li>{product_name} (ID: {product_id})</li>")
        html_parts.append("</ul>")
    else:
        html_parts.append("<p>No products found for the given tags.</p>")

    html_parts.extend(["</body>", "</html>"])
    html_content = "\n".join(html_parts)

    return HTMLResponse(content=html_content, status_code=200)


if __name__ == "__main__":
    import uvicorn

    # Run the application directly; binding to 0.0.0.0:5000 as required.
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)