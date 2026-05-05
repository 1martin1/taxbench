import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Tuple

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse


DB_PATH = "db.sqlite3"

MAX_NAME_LENGTH = 256
MAX_FIELD_NAME_LENGTH = 128
MAX_FIELD_VALUE_LENGTH = 4096
MAX_PRODUCT_FIELDS = 100
MAX_PRODUCT_JSON_BYTES = 64 * 1024
MAX_UPLOAD_DATA_BYTES = 64 * 1024
MAX_DOWNLOAD_DATA_BYTES = 64 * 1024
MAX_SEARCH_QUERY_LENGTH = 256
MAX_SEARCH_RESULTS = 100


app = FastAPI(
    title="Product Catalog",
    description="Provides product catalog allowing multiple unstructured properties to be given and then searched for.",
    version="1.0.11",
)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                name TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_products_name ON products(name)"
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def validate_field_name(key: Any, detail: str) -> str:
    if not isinstance(key, str):
        raise _bad_request(detail)
    if not key:
        raise _bad_request(detail)
    if len(key) > MAX_FIELD_NAME_LENGTH:
        raise _bad_request(detail)
    return key


def validate_field_value(value: Any, detail: str, *, is_name: bool = False) -> str:
    if not isinstance(value, str):
        raise _bad_request(detail)
    if is_name:
        if not value or len(value) > MAX_NAME_LENGTH:
            raise _bad_request(detail)
    else:
        if len(value) > MAX_FIELD_VALUE_LENGTH:
            raise _bad_request(detail)
    return value


def validate_product_payload(payload: Any, detail: str) -> Dict[str, str]:
    if not isinstance(payload, dict):
        raise _bad_request(detail)

    if "name" not in payload:
        raise _bad_request(detail)

    if len(payload) > MAX_PRODUCT_FIELDS:
        raise _bad_request(detail)

    validated: Dict[str, str] = {}
    for key, value in payload.items():
        validated_key = validate_field_name(key, detail)
        validated_value = validate_field_value(
            value, detail, is_name=(validated_key == "name")
        )
        validated[validated_key] = validated_value

    if "name" not in validated:
        raise _bad_request(detail)

    return validated


def serialize_product(product: Dict[str, str], detail: str) -> str:
    data = json.dumps(product, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(data.encode("utf-8")) > MAX_PRODUCT_JSON_BYTES:
        raise _bad_request(detail)
    return data


def deserialize_product(data: str, detail: str) -> Dict[str, str]:
    if len(data.encode("utf-8")) > MAX_UPLOAD_DATA_BYTES:
        raise _bad_request(detail)

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise _bad_request(detail)

    return validate_product_payload(parsed, detail)


def parse_and_validate_stored_product(data: str) -> Dict[str, str]:
    if len(data.encode("utf-8")) > MAX_PRODUCT_JSON_BYTES:
        raise _bad_request("Invalid stored product data")

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise _bad_request("Invalid stored product data")

    return validate_product_payload(parsed, "Invalid stored product data")


def product_matches_query(product: Dict[str, str], needle: str) -> bool:
    for key, value in product.items():
        if needle in key.lower() or needle in value.lower():
            return True
    return False


def fetch_candidate_rows(conn: sqlite3.Connection, query: str) -> List[Tuple[str]]:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like_pattern = f"%{escaped}%"
    return conn.execute(
        """
        SELECT data
        FROM products
        WHERE name LIKE ? ESCAPE '\\'
        LIMIT ?
        """,
        (like_pattern, MAX_SEARCH_RESULTS),
    ).fetchall()


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.post("/add", status_code=201)
async def add_product(request: Request):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid input")

    product = validate_product_payload(payload, "Invalid input")
    serialized = serialize_product(product, "Invalid input")

    with get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM products WHERE name = ?",
            (product["name"],),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=400, detail="Invalid input")

        conn.execute(
            """
            INSERT INTO products (name, data)
            VALUES (?, ?)
            """,
            (product["name"], serialized),
        )

    return JSONResponse(status_code=201, content={"detail": "Product successfully added"})


@app.get("/download")
async def download_product(name: str = Query(..., min_length=1, max_length=MAX_NAME_LENGTH)):
    if not name:
        raise HTTPException(status_code=400, detail="Invalid input")

    with get_db() as conn:
        row = conn.execute(
            "SELECT data FROM products WHERE name = ?",
            (name,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=400, detail="Invalid input")

    data = row[0]
    if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_DOWNLOAD_DATA_BYTES:
        raise HTTPException(status_code=400, detail="Invalid input")

    parse_and_validate_stored_product(data)

    return {"data": data}


@app.post("/upload")
async def upload_product(request: Request):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid upload query")

    if set(payload.keys()) != {"name", "data"}:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    name = payload.get("name")
    data = payload.get("data")

    validate_field_name("name", "Invalid upload query")
    name = validate_field_value(name, "Invalid upload query", is_name=True)

    if not isinstance(data, str):
        raise HTTPException(status_code=400, detail="Invalid upload query")

    product = deserialize_product(data, "Invalid upload query")

    if product["name"] != name:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    serialized = serialize_product(product, "Invalid upload query")

    with get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO products (name, data)
            VALUES (?, ?)
            """,
            (name, serialized),
        )

    return {"detail": "The product with the given name was updated."}


@app.get("/search")
async def search_products(
    query: str = Query(..., min_length=1, max_length=MAX_SEARCH_QUERY_LENGTH)
):
    if not isinstance(query, str) or not query.strip():
        raise HTTPException(status_code=400, detail="Invalid search query")

    needle = query.lower()
    results: List[Dict[str, str]] = []

    with get_db() as conn:
        candidate_rows = fetch_candidate_rows(conn, query)

        if len(candidate_rows) < MAX_SEARCH_RESULTS:
            remaining = MAX_SEARCH_RESULTS - len(candidate_rows)
            extra_rows = conn.execute(
                """
                SELECT data
                FROM products
                WHERE name NOT LIKE ? ESCAPE '\\'
                LIMIT ?
                """,
                (
                    f"%{query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')}%",
                    remaining,
                ),
            ).fetchall()
            rows = candidate_rows + extra_rows
        else:
            rows = candidate_rows

    for (data,) in rows:
        try:
            product = parse_and_validate_stored_product(data)
        except HTTPException:
            continue

        if product_matches_query(product, needle):
            results.append(product)
            if len(results) >= MAX_SEARCH_RESULTS:
                break

    return {"results": results}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)