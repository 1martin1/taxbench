import html
import sqlite3
from contextlib import closing
from typing import List

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import JSONResponse

DB_PATH = "db.sqlite3"

MAX_QUERY_TAGS = 50
MAX_QUERY_TAG_LENGTH = 100
MAX_PRODUCT_NAME_LENGTH = 255
MAX_POST_TAGS = 50
MAX_RESULTS = 100


app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
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


@app.on_event("startup")
def on_startup() -> None:
    init_db()


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_name: str = Field(..., min_length=1, max_length=MAX_PRODUCT_NAME_LENGTH)
    tags: List[str]

    @field_validator("product_name")
    @classmethod
    def validate_product_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("product_name must not be empty")
        if len(value) > MAX_PRODUCT_NAME_LENGTH:
            raise ValueError(f"product_name must be at most {MAX_PRODUCT_NAME_LENGTH} characters")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: List[str]) -> List[str]:
        if not isinstance(value, list) or len(value) == 0:
            raise ValueError("tags must be a non-empty array of strings")
        if len(value) > MAX_POST_TAGS:
            raise ValueError(f"tags must contain at most {MAX_POST_TAGS} items")

        cleaned = []
        seen = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("each tag must be a string")
            tag = item.strip()
            if not tag:
                raise ValueError("tags must not contain empty strings")
            if len(tag) > MAX_QUERY_TAG_LENGTH:
                raise ValueError(f"each tag must be at most {MAX_QUERY_TAG_LENGTH} characters")
            lowered = tag.lower()
            if lowered not in seen:
                seen.add(lowered)
                cleaned.append(lowered)

        if not cleaned:
            raise ValueError("tags must contain at least one non-empty string")

        return cleaned


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": "Invalid input"})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


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
def get_recommendations(
    tags: str = Query(..., description="Tags to search for separated by comma.")
) -> HTMLResponse:
    if len(tags) > (MAX_QUERY_TAGS * (MAX_QUERY_TAG_LENGTH + 1)):
        raise HTTPException(status_code=400, detail="tags query parameter is too large")

    parsed_tags = []
    seen = set()
    for raw_tag in tags.split(","):
        tag = raw_tag.strip().lower()
        if not tag:
            continue
        if len(tag) > MAX_QUERY_TAG_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"each tag must be at most {MAX_QUERY_TAG_LENGTH} characters",
            )
        if tag not in seen:
            seen.add(tag)
            parsed_tags.append(tag)
            if len(parsed_tags) > MAX_QUERY_TAGS:
                raise HTTPException(
                    status_code=400,
                    detail=f"at most {MAX_QUERY_TAGS} tags are allowed",
                )

    if not parsed_tags:
        raise HTTPException(status_code=400, detail="tags query parameter must contain at least one tag")

    placeholders = ",".join("?" for _ in parsed_tags)
    query = f"""
        SELECT DISTINCT p.id, p.product_name
        FROM products p
        INNER JOIN product_tags pt ON pt.product_id = p.id
        WHERE pt.tag IN ({placeholders})
        ORDER BY p.id ASC
        LIMIT ?
    """

    with closing(get_db_connection()) as conn:
        rows = conn.execute(query, [*parsed_tags, MAX_RESULTS]).fetchall()

    items = []
    for row in rows:
        safe_name = html.escape(row["product_name"])
        items.append(f"<li>{safe_name}</li>")

    if items:
        body = (
            "<!DOCTYPE html>"
            "<html><head><meta charset='utf-8'><title>Recommendations</title></head>"
            "<body>"
            "<h1>Recommended Products</h1>"
            "<ul>"
            + "".join(items)
            + "</ul>"
            "</body></html>"
        )
    else:
        body = (
            "<!DOCTYPE html>"
            "<html><head><meta charset='utf-8'><title>Recommendations</title></head>"
            "<body>"
            "<h1>Recommended Products</h1>"
            "<p>No matching products found.</p>"
            "</body></html>"
        )

    return HTMLResponse(content=body, status_code=200)


@app.post(
    "/recommender",
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
    },
)
def create_product(payload: ProductCreate = Body(...)) -> Response:
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO products (product_name) VALUES (?)",
                (payload.product_name,),
            )
            product_id = cursor.lastrowid

            cursor.executemany(
                "INSERT INTO product_tags (product_id, tag) VALUES (?, ?)",
                [(product_id, tag) for tag in payload.tags],
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid input")
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error")

    return Response(status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)