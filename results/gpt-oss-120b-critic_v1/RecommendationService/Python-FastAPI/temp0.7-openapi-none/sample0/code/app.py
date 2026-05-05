import os
import sqlite3
from typing import List

from fastapi import FastAPI, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, conlist, constr, validator
from jinja2 import Environment, select_autoescape
import uvicorn

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("APP_SECRET environment variable must be set for security reasons.")

DB_PATH = "db.sqlite3"

# Limits to mitigate resource‑exhaustion attacks
MAX_TAGS_PER_REQUEST = 20
MAX_TAG_LENGTH = 30
MAX_PRODUCT_NAME_LENGTH = 100
MAX_TAGS_PER_PRODUCT = 20

# ----------------------------------------------------------------------
# SQL statements
# ----------------------------------------------------------------------
CREATE_PRODUCTS_TABLE = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL
);
"""

CREATE_TAGS_TABLE = """
CREATE TABLE IF NOT EXISTS product_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);
"""

INSERT_PRODUCT_SQL = "INSERT INTO products (product_name) VALUES (?);"
INSERT_TAG_SQL = "INSERT INTO product_tags (product_id, tag) VALUES (?, ?);"
SELECT_PRODUCTS_BY_TAGS_SQL = """
SELECT DISTINCT p.id, p.product_name
FROM products p
JOIN product_tags t ON p.id = t.product_id
WHERE t.tag IN ({placeholders})
ORDER BY p.product_name;
"""
SELECT_TAGS_FOR_PRODUCT_SQL = """
SELECT tag FROM product_tags WHERE product_id = ?;
"""

# ----------------------------------------------------------------------
# HTML template with auto‑escaping enabled
# ----------------------------------------------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Product Recommendations</title>
    <meta charset="utf-8"/>
</head>
<body>
    <h1>Products matching tags: {{ tags|join(', ') }}</h1>
    {% if products %}
        <ul>
        {% for product in products %}
            <li>{{ product.name }} (Tags: {{ product.tags|join(', ') }})</li>
        {% endfor %}
        </ul>
    {% else %}
        <p>No products found.</p>
    {% endif %}
</body>
</html>
"""

jinja_env = Environment(autoescape=select_autoescape(enabled_extensions=('html', 'htm', 'xml')))
template = jinja_env.from_string(HTML_TEMPLATE)

# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)


def get_db_connection() -> sqlite3.Connection:
    """Create a new SQLite connection that can be shared across threads."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the SQLite database with required tables."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(CREATE_PRODUCTS_TABLE)
        cur.execute(CREATE_TAGS_TABLE)
        conn.commit()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class ProductCreate(BaseModel):
    product_name: constr(min_length=1, max_length=MAX_PRODUCT_NAME_LENGTH)
    tags: conlist(
        item_type=constr(min_length=1, max_length=MAX_TAG_LENGTH),
        min_items=1,
        max_items=MAX_TAGS_PER_PRODUCT,
    )

    @validator("tags", each_item=True)
    def strip_tags(cls, v: str) -> str:
        return v.strip()


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/recommender",
    status_code=200,
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
    },
)
def post_product(payload: ProductCreate):
    """Create a new product with associated tags."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(INSERT_PRODUCT_SQL, (payload.product_name,))
        product_id = cur.lastrowid

        tag_rows = [(product_id, tag) for tag in payload.tags if tag]
        cur.executemany(INSERT_TAG_SQL, tag_rows)
        conn.commit()

    return JSONResponse(content={"message": "Product created successfully"})


@app.get(
    "/recommender",
    response_class=Response,
    responses={
        200: {
            "description": "A list of products that match one of the provided tags together with the links in HTML.",
            "content": {"text/html": {}},
        },
        400: {"description": "Bad request"},
    },
)
def get_products(
    tags: str = Query(..., description="Tags to search for separated by comma.")
):
    """Retrieve all posted products that match at least one of the provided tags."""
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()]

    if not tag_list:
        raise HTTPException(status_code=400, detail="At least one tag must be provided.")

    if len(tag_list) > MAX_TAGS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Number of tags must not exceed {MAX_TAGS_PER_REQUEST}.",
        )

    placeholders = ", ".join(["?"] * len(tag_list))

    with get_db_connection() as conn:
        cur = conn.cursor()
        query = SELECT_PRODUCTS_BY_TAGS_SQL.format(placeholders=placeholders)
        cur.execute(query, tag_list)
        product_rows = cur.fetchall()

        products = []
        for row in product_rows:
            product_id = row["id"]
            cur.execute(SELECT_TAGS_FOR_PRODUCT_SQL, (product_id,))
            tags_for_product = [t["tag"] for t in cur.fetchall()]
            products.append({"name": row["product_name"], "tags": tags_for_product})

    rendered_html = template.render(products=products, tags=tag_list)
    return Response(content=rendered_html, media_type="text/html")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)