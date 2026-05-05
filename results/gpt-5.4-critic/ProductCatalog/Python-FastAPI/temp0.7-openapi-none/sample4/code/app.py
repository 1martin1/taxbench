import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Generator, List

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

DB_PATH = "db.sqlite3"

MAX_REQUEST_BODY_BYTES = 64 * 1024
MAX_PRODUCT_FIELDS = 128
MAX_FIELD_NAME_LENGTH = 128
MAX_FIELD_VALUE_LENGTH = 4096
MAX_STORED_DATA_BYTES = 64 * 1024
MAX_SEARCH_QUERY_LENGTH = 256
MAX_SEARCH_RESULTS = 200

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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_name ON products(name)")
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _read_json_body_with_limit(request: Request, error_detail: str) -> Any:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                raise HTTPException(status_code=400, detail=error_detail)
        except ValueError:
            raise HTTPException(status_code=400, detail=error_detail)

    try:
        body = request.scope.get("_cached_body")
        if body is None:
            body = b""
            received = 0
            more_body = True
            while more_body:
                message = request.receive()
                if hasattr(message, "__await__"):
                    raise RuntimeError("Synchronous body reader used in async context")
                raise RuntimeError("Unexpected request.receive handling")
    except RuntimeError:
        pass

    # FastAPI/Starlette request body reading must be async; this helper is only a placeholder
    # and is not used directly.


async def read_json_body_with_limit(request: Request, error_detail: str) -> Any:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                raise HTTPException(status_code=400, detail=error_detail)
        except ValueError:
            raise HTTPException(status_code=400, detail=error_detail)

    body = await request.body()
    if len(body) > MAX_REQUEST_BODY_BYTES:
        raise HTTPException(status_code=400, detail=error_detail)

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail=error_detail)

    return payload


def _validate_field_name(key: Any, error_detail: str) -> str:
    if not isinstance(key, str):
        raise HTTPException(status_code=400, detail=error_detail)
    if key == "":
        raise HTTPException(status_code=400, detail=error_detail)
    if len(key) > MAX_FIELD_NAME_LENGTH:
        raise HTTPException(status_code=400, detail=error_detail)
    return key


def _validate_field_value(value: Any, error_detail: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=error_detail)
    if len(value) > MAX_FIELD_VALUE_LENGTH:
        raise HTTPException(status_code=400, detail=error_detail)
    return value


def validate_product_payload(payload: Any) -> Dict[str, str]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid input")

    if "name" not in payload:
        raise HTTPException(status_code=400, detail="Invalid input")

    if len(payload) > MAX_PRODUCT_FIELDS:
        raise HTTPException(status_code=400, detail="Invalid input")

    validated: Dict[str, str] = {}
    for key, value in payload.items():
        normalized_key = _validate_field_name(key, "Invalid input")
        normalized_value = _validate_field_value(value, "Invalid input")
        validated[normalized_key] = normalized_value

    if not validated["name"]:
        raise HTTPException(status_code=400, detail="Invalid input")

    serialized = serialize_product(validated)
    if len(serialized.encode("utf-8")) > MAX_STORED_DATA_BYTES:
        raise HTTPException(status_code=400, detail="Invalid input")

    return validated


def serialize_product(product: Dict[str, str]) -> str:
    return json.dumps(product, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def deserialize_product(data: str) -> Dict[str, str]:
    if not isinstance(data, str):
        raise HTTPException(status_code=400, detail="Invalid upload query")
    if len(data.encode("utf-8")) > MAX_STORED_DATA_BYTES:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Invalid upload query")

    if len(parsed) > MAX_PRODUCT_FIELDS:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    validated: Dict[str, str] = {}
    for key, value in parsed.items():
        normalized_key = _validate_field_name(key, "Invalid upload query")
        normalized_value = _validate_field_value(value, "Invalid upload query")
        validated[normalized_key] = normalized_value

    if "name" not in validated or not validated["name"]:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    serialized = serialize_product(validated)
    if len(serialized.encode("utf-8")) > MAX_STORED_DATA_BYTES:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    return validated


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.post("/add", status_code=201)
async def add_product(request: Request):
    payload = await read_json_body_with_limit(request, "Invalid input")
    product = validate_product_payload(payload)
    data = serialize_product(product)

    with get_db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO products (name, data)
                VALUES (?, ?)
                """,
                (product["name"], data),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Invalid input")

    return JSONResponse(status_code=201, content={"message": "Product successfully added"})


@app.get("/download")
def download_product(name: str = Query(..., description="The name of the product")):
    if not isinstance(name, str) or name == "" or len(name) > MAX_FIELD_VALUE_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    with get_db() as conn:
        row = conn.execute(
            "SELECT data FROM products WHERE name = ?",
            (name,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Product not found")

    data = row["data"]
    if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_STORED_DATA_BYTES:
        raise HTTPException(status_code=400, detail="Invalid input")

    return {"data": data}


@app.post("/upload")
async def upload_product(request: Request):
    payload = await read_json_body_with_limit(request, "Invalid upload query")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid upload query")

    allowed_keys = {"name", "data"}
    if set(payload.keys()) != allowed_keys:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    name = payload.get("name")
    data = payload.get("data")

    if not isinstance(name, str) or not name or len(name) > MAX_FIELD_VALUE_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid upload query")
    if not isinstance(data, str):
        raise HTTPException(status_code=400, detail="Invalid upload query")
    if len(data.encode("utf-8")) > MAX_STORED_DATA_BYTES:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    product = deserialize_product(data)

    if product["name"] != name:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    serialized = serialize_product(product)

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO products (name, data)
            VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET data = excluded.data
            """,
            (name, serialized),
        )

    return {"message": "The product with the given name was updated."}


@app.get("/search")
def search_products(query: str = Query(..., description="A search query to find matching products.")):
    if not isinstance(query, str) or query == "" or len(query) > MAX_SEARCH_QUERY_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid search query")

    query_lower = query.lower()
    results: List[Dict[str, str]] = []

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT data
            FROM products
            WHERE lower(name) LIKE ?
               OR lower(data) LIKE ?
            LIMIT ?
            """,
            (f"%{query_lower}%", f"%{query_lower}%", MAX_SEARCH_RESULTS),
        ).fetchall()

    for row in rows:
        try:
            product = json.loads(row["data"])
        except Exception:
            continue

        if not isinstance(product, dict):
            continue

        normalized_product: Dict[str, str] = {}
        for key, value in product.items():
            if isinstance(key, str) and isinstance(value, str):
                normalized_product[key] = value

        if "name" not in normalized_product or not normalized_product["name"]:
            continue

        values = [value for key, value in normalized_product.items() if isinstance(value, str)]
        if any(query_lower in value.lower() for value in values):
            results.append(normalized_product)

    return {"results": results}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)