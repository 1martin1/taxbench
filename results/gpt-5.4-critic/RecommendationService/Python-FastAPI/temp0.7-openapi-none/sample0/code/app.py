import html
import os
import sqlite3
import threading
import time
from contextlib import closing
from typing import List

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, field_validator

DB_PATH = "db.sqlite3"

MAX_PRODUCT_NAME_LENGTH = 255
MAX_TAG_LENGTH = 64
MAX_TAGS_PER_POST = 50
MAX_QUERY_TAGS = 50
MAX_TAGS_QUERY_LENGTH = 2048
MAX_RESULTS = 500

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_GET_MAX = 120
RATE_LIMIT_POST_MAX = 30


class SimpleRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets = {}

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None or now >= bucket["reset_at"]:
                self._buckets[key] = {"count": 1, "reset_at": now + window_seconds}
                self._cleanup(now)
                return True

            if bucket["count"] >= limit:
                self._cleanup(now)
                return False

            bucket["count"] += 1
            self._cleanup(now)
            return True

    def _cleanup(self, now: float) -> None:
        if len(self._buckets) <= 1024:
            return
        expired_keys = [key for key, value in self._buckets.items() if now >= value["reset_at"]]
        for key in expired_keys:
            self._buckets.pop(key, None)


rate_limiter = SimpleRateLimiter()

app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(get_connection()) as conn:
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
def startup() -> None:
    init_db()


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_name: str
    tags: List[str]

    @field_validator("product_name")
    @classmethod
    def validate_product_name(cls, value: str) -> str:
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
    def validate_tags(cls, value: List[str]) -> List[str]:
        if not isinstance(value, list):
            raise ValueError("tags must be an array")
        if len(value) > MAX_TAGS_PER_POST:
            raise ValueError("too many tags")
        cleaned = []
        seen = set()
        for tag in value:
            if not isinstance(tag, str):
                raise ValueError("each tag must be a string")
            normalized = tag.strip().lower()
            if not normalized:
                raise ValueError("tags must not contain empty values")
            if len(normalized) > MAX_TAG_LENGTH:
                raise ValueError("tag is too long")
            if normalized not in seen:
                seen.add(normalized)
                cleaned.append(normalized)
        return cleaned


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or "unknown"
    client = request.client
    return client.host if client and client.host else "unknown"


def enforce_rate_limit(request: Request, scope: str, limit: int) -> None:
    client_ip = get_client_ip(request)
    key = f"{scope}:{client_ip}"
    if not rate_limiter.allow(key, limit, RATE_LIMIT_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too Many Requests")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": "Invalid input"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.post(
    "/recommender",
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
    },
)
def post_product(request: Request, payload: ProductCreate = Body(...)):
    enforce_rate_limit(request, "POST:/recommender", RATE_LIMIT_POST_MAX)

    if len(payload.tags) == 0:
        raise HTTPException(status_code=400, detail="Invalid input")

    try:
        with closing(get_connection()) as conn:
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
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Invalid input")

    return PlainTextResponse(content="", status_code=200)


@app.get("/recommender", response_class=HTMLResponse)
def get_recommendations(
    request: Request,
    tags: str = Query(..., description="Tags to search for separated by comma."),
):
    enforce_rate_limit(request, "GET:/recommender", RATE_LIMIT_GET_MAX)

    if len(tags) > MAX_TAGS_QUERY_LENGTH:
        raise HTTPException(status_code=400, detail="tags query parameter is too long")

    parsed_tags = []
    seen = set()

    for raw_tag in tags.split(","):
        normalized = raw_tag.strip().lower()
        if not normalized:
            continue
        if len(normalized) > MAX_TAG_LENGTH:
            raise HTTPException(status_code=400, detail="tags query parameter contains an invalid tag")
        if normalized not in seen:
            seen.add(normalized)
            parsed_tags.append(normalized)
            if len(parsed_tags) > MAX_QUERY_TAGS:
                raise HTTPException(status_code=400, detail="too many tags in query parameter")

    if not parsed_tags:
        raise HTTPException(status_code=400, detail="tags query parameter must contain at least one tag")

    placeholders = ",".join("?" for _ in parsed_tags)

    try:
        with closing(get_connection()) as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT p.product_name
                FROM products p
                JOIN product_tags pt ON pt.product_id = p.id
                WHERE pt.tag IN ({placeholders})
                ORDER BY p.id ASC
                LIMIT ?
                """,
                [*parsed_tags, MAX_RESULTS],
            ).fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal server error")

    html_parts = [
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