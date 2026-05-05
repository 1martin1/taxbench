import html
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Generator, List

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict

DB_PATH = "db.sqlite3"

# Resource limits to improve robustness without changing the API contract.
MAX_PRODUCT_NAME_LENGTH = 1024
MAX_TAG_LENGTH = 256
MAX_POST_TAGS = 100
MAX_GET_QUERY_LENGTH = 4096
MAX_GET_TAGS = 100
MAX_RESULTS = 500
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60
DB_BUSY_TIMEOUT_MS = 5000

_rate_limit_lock = threading.Lock()
_rate_limit_state = {}

app = FastAPI(
    title="Recommendation",
    description="Product recommendation service to provide a list of posted products searched by tag.",
    version="1.0.11",
)


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    product_name: str
    tags: List[str]


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def check(self, key: str) -> None:
        now = time.time()
        with _rate_limit_lock:
            entries = _rate_limit_state.get(key, [])
            cutoff = now - self.window_seconds
            entries = [ts for ts in entries if ts > cutoff]
            if len(entries) >= self.max_requests:
                raise HTTPException(status_code=429, detail="Too Many Requests")
            entries.append(now)
            _rate_limit_state[key] = entries


rate_limiter = RateLimiter(
    max_requests=RATE_LIMIT_REQUESTS,
    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
)


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, timeout=DB_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
        yield conn
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
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS product_tags (
                product_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (product_id, tag_id),
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_tags_tag_id ON product_tags(tag_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_tags_product_id ON product_tags(product_id)"
        )
        conn.commit()


@app.on_event("startup")
def startup_event() -> None:
    init_db()


def normalize_tag(tag: str) -> str:
    return tag.strip().lower()


def get_client_key(request: Request) -> str:
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def validate_post_payload(payload: ProductCreate) -> tuple[str, List[str]]:
    if not isinstance(payload.product_name, str):
        raise HTTPException(status_code=400, detail="Invalid input")
    if not isinstance(payload.tags, list):
        raise HTTPException(status_code=400, detail="Invalid input")

    if len(payload.product_name) > MAX_PRODUCT_NAME_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")
    if len(payload.tags) > MAX_POST_TAGS:
        raise HTTPException(status_code=400, detail="Invalid input")

    normalized_tags: List[str] = []
    seen = set()

    for tag in payload.tags:
        if not isinstance(tag, str):
            raise HTTPException(status_code=400, detail="Invalid input")
        if len(tag) > MAX_TAG_LENGTH:
            raise HTTPException(status_code=400, detail="Invalid input")
        normalized = normalize_tag(tag)
        if normalized not in seen:
            seen.add(normalized)
            normalized_tags.append(normalized)

    return payload.product_name, normalized_tags


def parse_query_tags(tags: str) -> List[str]:
    if len(tags) > MAX_GET_QUERY_LENGTH:
        raise HTTPException(status_code=200, detail="")

    parsed_tags: List[str] = []
    seen = set()

    for raw_tag in tags.split(","):
        if len(raw_tag) > MAX_TAG_LENGTH:
            continue
        normalized = normalize_tag(raw_tag)
        if normalized and normalized not in seen:
            seen.add(normalized)
            parsed_tags.append(normalized)
            if len(parsed_tags) >= MAX_GET_TAGS:
                break

    return parsed_tags


@app.post("/recommender", response_class=PlainTextResponse)
def post_product(
    request: Request,
    payload: ProductCreate = Body(...),
) -> PlainTextResponse:
    rate_limiter.check(get_client_key(request))
    product_name, normalized_tags = validate_post_payload(payload)

    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO products (product_name) VALUES (?)",
                (product_name,),
            )
            product_id = cursor.lastrowid

            for tag_name in normalized_tags:
                cursor.execute(
                    "INSERT OR IGNORE INTO tags (name) VALUES (?)",
                    (tag_name,),
                )
                cursor.execute(
                    "SELECT id FROM tags WHERE name = ? COLLATE NOCASE",
                    (tag_name,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("Failed to resolve tag id")
                tag_id = row["id"]
                cursor.execute(
                    "INSERT OR IGNORE INTO product_tags (product_id, tag_id) VALUES (?, ?)",
                    (product_id, tag_id),
                )

            conn.commit()
    except HTTPException:
        raise
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Invalid input")
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal Server Error")
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Server Error")

    return PlainTextResponse(content="", status_code=200)


@app.get("/recommender", response_class=HTMLResponse)
def get_recommendations(
    request: Request,
    tags: str = Query(..., description="Tags to search for separated by comma."),
) -> HTMLResponse:
    rate_limiter.check(get_client_key(request))
    parsed_tags = parse_query_tags(tags)

    searched = ", ".join(html.escape(tag) for tag in parsed_tags)

    if not parsed_tags:
        body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Recommendation Results</title>
</head>
<body>
    <h1>Recommended Products</h1>
    <p>Searched tags: {searched}</p>
    <ul></ul>
</body>
</html>"""
        return HTMLResponse(content=body, status_code=200)

    placeholders = ",".join("?" for _ in parsed_tags)
    query = f"""
        SELECT DISTINCT p.id, p.product_name
        FROM products p
        JOIN product_tags pt ON p.id = pt.product_id
        JOIN tags t ON t.id = pt.tag_id
        WHERE lower(t.name) IN ({placeholders})
        ORDER BY p.id ASC
        LIMIT ?
    """

    try:
        with get_db() as conn:
            rows = conn.execute(query, [*parsed_tags, MAX_RESULTS]).fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Internal Server Error")

    items = []
    for row in rows:
        product_name = html.escape(row["product_name"])
        items.append(f"<li>{product_name}</li>")

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Recommendation Results</title>
</head>
<body>
    <h1>Recommended Products</h1>
    <p>Searched tags: {searched}</p>
    <ul>
        {''.join(items)}
    </ul>
</body>
</html>"""

    return HTMLResponse(content=body, status_code=200)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path == "/recommender" and request.method == "GET":
        content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Error</title>
</head>
<body>
    <h1>Error</h1>
    <p>{html.escape(str(exc.detail))}</p>
</body>
</html>"""
        return HTMLResponse(content=content, status_code=exc.status_code)
    return PlainTextResponse(
        content="" if exc.status_code == 200 else str(exc.detail),
        status_code=exc.status_code,
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)