import os
import sqlite3
import html
from typing import List, Generator

from fastapi import FastAPI, HTTPException, Query, Depends, Response
from pydantic import BaseModel, Field, conlist, constr, validator
import uvicorn

# ----------------------------------------------------------------------
# Configuration and constants
# ----------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "db.sqlite3")

MAX_TAGS = 20               # Maximum number of tags per request/product
MAX_TAG_LENGTH = 50         # Maximum characters per tag
MAX_PRODUCT_NAME_LENGTH = 100  # Maximum characters for product name

# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Dependency that provides a SQLite connection.
    The connection lives for the duration of the request.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """
    Initialise the SQLite database with the required tables.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


# ----------------------------------------------------------------------
# Pydantic models with validation
# ----------------------------------------------------------------------
class ProductCreate(BaseModel):
    product_name: constr(strip_whitespace=True, min_length=1, max_length=MAX_PRODUCT_NAME_LENGTH) = Field(
        ..., description="Name of the product"
    )
    tags: conlist(
        item_type=constr(strip_whitespace=True, min_length=1, max_length=MAX_TAG_LENGTH),
        min_items=1,
        max_items=MAX_TAGS,
    ) = Field(..., description="List of tags associated with the product")

    @validator("tags", each_item=True)
    def no_empty_tag(cls, v):
        if not v:
            raise ValueError("Tag cannot be empty")
        return v


# ----------------------------------------------------------------------
# FastAPI application
# ----------------------------------------------------------------------
app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)


@app.on_event("startup")
def on_startup():
    # Initialise the DB when the application starts
    init_db()


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
@app.post(
    "/recommender",
    summary="Post a new product along with its tags.",
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
    },
)
def create_product(
    payload: ProductCreate, db: sqlite3.Connection = Depends(get_db)
):
    """
    Insert a new product and its tags into the database.
    Returns 200 on success, 400 for invalid input.
    """
    try:
        cursor = db.cursor()
        # Insert product
        cursor.execute(
            "INSERT INTO products (name) VALUES (?)",
            (payload.product_name,),
        )
        product_id = cursor.lastrowid

        # Insert tags
        tag_rows = [(product_id, tag) for tag in payload.tags]
        cursor.executemany(
            "INSERT INTO tags (product_id, tag) VALUES (?, ?)",
            tag_rows,
        )
        db.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Return plain text to stay compatible with the minimal OpenAPI spec
    return Response(content="Product created successfully.", media_type="text/plain")


@app.get(
    "/recommender",
    summary="Get all posted products that match at least one of the provided tags.",
    responses={200: {"description": "HTML list of matching products"}},
)
def get_products_by_tags(
    tags: str = Query(..., description="Tags to search for separated by comma."),
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Retrieve products that have at least one tag matching any of the supplied tags.
    Returns an HTML page with a simple list of product links.
    """
    # Parse the incoming comma‑separated tags
    requested_tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not requested_tags:
        raise HTTPException(status_code=400, detail="At least one non‑empty tag must be provided.")
    if len(requested_tags) > MAX_TAGS:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_TAGS} tags are allowed.")
    for tag in requested_tags:
        if len(tag) > MAX_TAG_LENGTH:
            raise HTTPException(status_code=400, detail=f"Tag length must not exceed {MAX_TAG_LENGTH} characters.")

    # Build a parameterised query with the appropriate number of placeholders
    placeholders = ", ".join(["?"] * len(requested_tags))
    query = f"""
        SELECT DISTINCT p.id, p.name
        FROM products p
        JOIN tags t ON p.id = t.product_id
        WHERE t.tag IN ({placeholders})
        ORDER BY p.id ASC
    """

    cursor = db.cursor()
    cursor.execute(query, requested_tags)
    rows = cursor.fetchall()

    # Build HTML response with proper escaping
    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>Product Recommendations</title>",
        "</head>",
        "<body>",
        "<h1>Matching Products</h1>",
    ]

    if rows:
        html_parts.append("<ul>")
        for row in rows:
            product_id = row["id"]
            product_name = html.escape(row["name"])
            html_parts.append(
                f'<li><a href="/product/{product_id}">{product_name}</a></li>'
            )
        html_parts.append("</ul>")
    else:
        html_parts.append("<p>No products match the supplied tags.</p>")

    html_parts.extend(["</body>", "</html>"])
    html_content = "\n".join(html_parts)

    return Response(content=html_content, media_type="text/html")


# ----------------------------------------------------------------------
# Optional placeholder product detail route (not part of the spec but useful for the generated links)
# ----------------------------------------------------------------------
@app.get(
    "/product/{product_id}",
    include_in_schema=False,
    response_class=Response,
    summary="Placeholder product detail page.",
)
def product_detail(product_id: int, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute(
        "SELECT name FROM products WHERE id = ?",
        (product_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found.")
    product_name = html.escape(row["name"])
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><title>{product_name}</title></head>
    <body>
        <h1>{product_name}</h1>
        <p>Details for product ID {product_id} would appear here.</p>
        <a href="/recommender?tags=">Back to search</a>
    </body>
    </html>
    """
    return Response(content=html, media_type="text/html")


# ----------------------------------------------------------------------
# Application entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Use the secret if needed for future extensions; currently not used directly.
    _app_secret = os.getenv("APP_SECRET", "default-secret")
    uvicorn.run(app, host="0.0.0.0", port=5000)