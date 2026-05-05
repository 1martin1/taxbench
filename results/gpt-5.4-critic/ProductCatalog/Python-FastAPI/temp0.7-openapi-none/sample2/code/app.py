import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

DB_PATH = "db.sqlite3"

MAX_JSON_BODY_BYTES = 64 * 1024
MAX_PRODUCT_PROPERTIES = 128
MAX_FIELD_NAME_LENGTH = 128
MAX_FIELD_VALUE_LENGTH = 4096
MAX_DOWNLOAD_NAME_LENGTH = 256
MAX_SEARCH_QUERY_LENGTH = 256
MAX_SEARCH_RESULTS = 100

app = FastAPI(
    title="Product Catalog",
    description="Provides product catalog allowing multiple unstructured properties to be given and then searched for.",
    version="1.0.11",
)


class UploadRequest(BaseModel):
    name: str
    data: str
    model_config = ConfigDict(extra="forbid")


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
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _read_json_body_sync(body: bytes, invalid_detail: str) -> Any:
    if len(body) > MAX_JSON_BODY_BYTES:
        raise HTTPException(status_code=400, detail=invalid_detail)
    try:
        return json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail=invalid_detail)


def _validate_key(key: Any, invalid_detail: str) -> str:
    if not isinstance(key, str):
        raise HTTPException(status_code=400, detail=invalid_detail)
    if not key or len(key) > MAX_FIELD_NAME_LENGTH:
        raise HTTPException(status_code=400, detail=invalid_detail)
    return key


def _validate_value(value: Any, invalid_detail: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=invalid_detail)
    if len(value) > MAX_FIELD_VALUE_LENGTH:
        raise HTTPException(status_code=400, detail=invalid_detail)
    return value


def validate_product_payload(payload: Any) -> Dict[str, str]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid input")

    if "name" not in payload:
        raise HTTPException(status_code=400, detail="Invalid input")

    if len(payload) > MAX_PRODUCT_PROPERTIES:
        raise HTTPException(status_code=400, detail="Invalid input")

    validated: Dict[str, str] = {}
    for key, value in payload.items():
        normalized_key = _validate_key(key, "Invalid input")
        normalized_value = _validate_value(value, "Invalid input")
        validated[normalized_key] = normalized_value

    if validated["name"] == "" or len(validated["name"]) > MAX_DOWNLOAD_NAME_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    if "data" in validated and validated["data"] != "":
        raise HTTPException(status_code=400, detail="Invalid input")

    return validated


def serialize_product(product: Dict[str, str]) -> str:
    return json.dumps(product, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def deserialize_product(data: str) -> Dict[str, str]:
    if len(data.encode("utf-8")) > MAX_JSON_BODY_BYTES:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Invalid upload query")

    if len(parsed) > MAX_PRODUCT_PROPERTIES:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    validated: Dict[str, str] = {}
    for key, value in parsed.items():
        normalized_key = _validate_key(key, "Invalid upload query")
        normalized_value = _validate_value(value, "Invalid upload query")
        validated[normalized_key] = normalized_value

    if "name" not in validated or not validated["name"] or len(validated["name"]) > MAX_DOWNLOAD_NAME_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    if "data" in validated and validated["data"] != "":
        raise HTTPException(status_code=400, detail="Invalid upload query")

    return validated


def _insert_product(name: str, data: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO products (name, data) VALUES (?, ?)",
            (name, data),
        )
        return cursor.rowcount == 1


def _get_product_data(name: str):
    with get_db() as conn:
        return conn.execute(
            "SELECT data FROM products WHERE name = ?",
            (name,),
        ).fetchone()


def _product_exists(name: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM products WHERE name = ?",
            (name,),
        ).fetchone()
        return row is not None


def _update_product(name: str, data: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE products SET data = ? WHERE name = ?",
            (data, name),
        )
        return cursor.rowcount == 1


def _search_products(query: str) -> List[Dict[str, str]]:
    lowered_query = query.lower()
    results: List[Dict[str, str]] = []

    with get_db() as conn:
        rows = conn.execute(
            "SELECT data FROM products WHERE LOWER(name) LIKE ? LIMIT ?",
            (f"%{lowered_query}%", MAX_SEARCH_RESULTS),
        ).fetchall()

    seen_names = set()

    for row in rows:
        try:
            product = json.loads(row["data"])
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid search query")

        if not isinstance(product, dict):
            raise HTTPException(status_code=400, detail="Invalid search query")

        validated: Dict[str, str] = {}
        for key, value in product.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise HTTPException(status_code=400, detail="Invalid search query")
            validated[key] = value

        product_name = validated.get("name")
        if not product_name:
            raise HTTPException(status_code=400, detail="Invalid search query")

        results.append(validated)
        seen_names.add(product_name)

    if len(results) >= MAX_SEARCH_RESULTS:
        return results

    with get_db() as conn:
        rows = conn.execute("SELECT data FROM products").fetchall()

    for row in rows:
        if len(results) >= MAX_SEARCH_RESULTS:
            break

        try:
            product = json.loads(row["data"])
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid search query")

        if not isinstance(product, dict):
            raise HTTPException(status_code=400, detail="Invalid search query")

        validated: Dict[str, str] = {}
        for key, value in product.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise HTTPException(status_code=400, detail="Invalid search query")
            validated[key] = value

        product_name = validated.get("name")
        if not product_name:
            raise HTTPException(status_code=400, detail="Invalid search query")

        if product_name in seen_names:
            continue

        matched = False
        for key, value in validated.items():
            if key == "name":
                continue
            if lowered_query in value.lower():
                matched = True
                break

        if matched:
            results.append(validated)
            seen_names.add(product_name)

    return results


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.post("/add", status_code=201)
async def add_product(request: Request):
    body = await request.body()
    payload = await run_in_threadpool(_read_json_body_sync, body, "Invalid input")
    product = validate_product_payload(payload)
    name = product["name"]
    data = serialize_product(product)

    inserted = await run_in_threadpool(_insert_product, name, data)
    if not inserted:
        raise HTTPException(status_code=400, detail="Invalid input")

    return JSONResponse(status_code=201, content={"detail": "Product successfully added"})


@app.get("/download")
async def download_product(name: str = Query(..., description="The name of the product")):
    if not isinstance(name, str) or not name or len(name) > MAX_DOWNLOAD_NAME_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    row = await run_in_threadpool(_get_product_data, name)
    if row is None:
        return {"data": ""}

    return {"data": row["data"]}


@app.post("/upload")
async def upload_product(upload: UploadRequest):
    if not upload.name or len(upload.name) > MAX_DOWNLOAD_NAME_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    product = await run_in_threadpool(deserialize_product, upload.data)

    if product.get("name") != upload.name:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    exists = await run_in_threadpool(_product_exists, upload.name)
    if not exists:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    normalized_data = serialize_product(product)
    updated = await run_in_threadpool(_update_product, upload.name, normalized_data)
    if not updated:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    return {"detail": "The product with the given name was updated."}


@app.get("/search")
async def search_products(query: str = Query(..., description="A search query to find matching products.")):
    if not isinstance(query, str) or not query or len(query) > MAX_SEARCH_QUERY_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid search query")

    results = await run_in_threadpool(_search_products, query)
    return {"results": results}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)