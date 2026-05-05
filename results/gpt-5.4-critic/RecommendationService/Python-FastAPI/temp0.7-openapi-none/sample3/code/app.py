import html
import os
import sqlite3
import threading
import time
from contextlib import closing
from typing import Any, Optional

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, field_validator

DB_PATH = "db.sqlite3"

MAX_REQUEST_BODY_BYTES = 16 * 1024
MAX_PRODUCT_NAME_LENGTH = 255
MAX_TAG_LENGTH = 64
MAX_TAGS_PER_PRODUCT = 50
MAX_QUERY_TAGS = 50
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 120

_rate_limit_lock = threading.Lock()
_rate_limit_store: dict[str, list[float]] = {}

app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    product_name: Optional[str] = None
    tags: Optional[list[str]] = None

    @field_validator("product_name")
    @classmethod
    def validate_product_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not isinstance(value, str):
            raise ValueError("product_name must be a string")
        value = value.strip()
        if not value:
            raise ValueError("product_name must not be empty")
        if len(value) > MAX_PRODUCT_NAME_LENGTH:
            raise ValueError("product_name is too long")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return value
        if not isinstance(value, list):
            raise ValueError("tags must be an array of strings")
        if len(value) > MAX_TAGS_PER_PRODUCT:
            raise ValueError("too many tags")
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("each tag must be a string")
            tag = item.strip()
            if not tag:
                raise ValueError("tags must not contain empty strings")
            if len(tag) > MAX_TAG_LENGTH:
                raise ValueError("tag is too long")
            lowered = tag.lower()
            if lowered not in seen:
                seen.add(lowered)
                cleaned.append(lowered)
        return cleaned


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
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


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    with _rate_limit_lock:
        timestamps = _rate_limit_store.get(client_ip, [])
        timestamps = [ts for ts in timestamps if ts >= window_start]
        if len(timestamps) >= RATE_LIMIT_MAX_REQUESTS:
            _rate_limit_store[client_ip] = timestamps
            return False
        timestamps.append(now)
        _rate_limit_store[client_ip] = timestamps
        if len(_rate_limit_store) > 10000:
            stale_keys = [
                key for key, values in _rate_limit_store.items() if not values or values[-1] < window_start
            ]
            for key in stale_keys:
                _rate_limit_store.pop(key, None)
        return True


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    client_ip = get_client_ip(request)
    if not check_rate_limit(client_ip):
        return PlainTextResponse("Too Many Requests", status_code=429)

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return PlainTextResponse("Invalid input", status_code=400)
        except ValueError:
            return PlainTextResponse("Invalid input", status_code=400)

    if request.method == "POST":
        body = await request.body()
        if len(body) > MAX_REQUEST_BODY_BYTES:
            return PlainTextResponse("Invalid input", status_code=400)

    response = await call_next(request)
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Invalid input"
    return PlainTextResponse(detail, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return PlainTextResponse("Invalid input", status_code=400)


@app.post(
    "/recommender",
    responses={
        200: {
            "description": "Successful operation",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {
            "description": "Invalid input",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
    },
    response_class=PlainTextResponse,
)
def post_product(payload: ProductCreate = Body(...)):
    if payload.product_name is None or payload.tags is None:
        raise HTTPException(status_code=400, detail="Invalid input")

    product_name = payload.product_name
    tags = payload.tags

    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO products (product_name) VALUES (?)",
                (product_name,),
            )
            product_id = cursor.lastrowid

            if tags:
                cursor.executemany(
                    "INSERT INTO product_tags (product_id, tag) VALUES (?, ?)",
                    [(product_id, tag) for tag in tags],
                )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid input")
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal Server Error")

    return PlainTextResponse(content="Successful operation", status_code=200)


@app.get("/recommender", response_class=HTMLResponse)
def get_recommendations(
    tags: str = Query(..., description="Tags to search for separated by comma.")
):
    if len(tags) > (MAX_QUERY_TAGS * (MAX_TAG_LENGTH + 1)):
        raise HTTPException(status_code=400, detail="Invalid input")

    parsed_tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in tags.split(","):
        tag = raw_tag.strip().lower()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise HTTPException(status_code=400, detail="Invalid input")
        if tag not in seen:
            seen.add(tag)
            parsed_tags.append(tag)
            if len(parsed_tags) > MAX_QUERY_TAGS:
                raise HTTPException(status_code=400, detail="Invalid input")

    if not parsed_tags:
        raise HTTPException(status_code=400, detail="Invalid input")

    placeholders = ",".join("?" for _ in parsed_tags)
    query = f"""
        SELECT DISTINCT p.id, p.product_name
        FROM products p
        INNER JOIN product_tags pt ON p.id = pt.product_id
        WHERE pt.tag IN ({placeholders})
        ORDER BY p.id ASC
    """

    try:
        with closing(get_db_connection()) as conn:
            rows = conn.execute(query, parsed_tags).fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal Server Error")

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

    if rows:
        html_parts.append("<ul>")
        for row in rows:
            product_name = html.escape(row["product_name"])
            html_parts.append(f"<li>{product_name}</li>")
        html_parts.append("</ul>")
    else:
        html_parts.append("<p>No matching products found.</p>")

    html_parts.append("</body>")
    html_parts.append("</html>")

    return HTMLResponse(content="".join(html_parts), status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)