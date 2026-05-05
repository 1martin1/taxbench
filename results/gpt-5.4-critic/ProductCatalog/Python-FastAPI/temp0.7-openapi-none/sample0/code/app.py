import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Generator, List

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import Annotated

DB_PATH = "db.sqlite3"

MAX_NAME_LENGTH = 256
MAX_PROPERTY_KEY_LENGTH = 128
MAX_PROPERTY_VALUE_LENGTH = 4096
MAX_ADDITIONAL_PROPERTIES = 100
MAX_SERIALIZED_PRODUCT_SIZE = 65536
MAX_UPLOAD_DATA_SIZE = 65536
MAX_DOWNLOAD_DATA_SIZE = 65536
MAX_SEARCH_QUERY_LENGTH = 256
MAX_SEARCH_RESULTS = 100
SQLITE_TEXT_LIMIT = 1_000_000

NameStr = Annotated[str, StringConstraints(min_length=1, max_length=MAX_NAME_LENGTH)]
QueryStr = Annotated[str, StringConstraints(min_length=1, max_length=MAX_SEARCH_QUERY_LENGTH)]
UploadDataStr = Annotated[str, StringConstraints(max_length=MAX_UPLOAD_DATA_SIZE)]


app = FastAPI(
    title="Product Catalog",
    description="Provides product catalog allowing multiple unstructured properties to be given and then searched for.",
    version="1.0.11",
)


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NameStr
    data: UploadDataStr


def _validate_product_dict(payload: Any, error_detail: str) -> Dict[str, str]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail=error_detail)

    if "name" not in payload:
        raise HTTPException(status_code=400, detail=error_detail)

    if len(payload) - 1 > MAX_ADDITIONAL_PROPERTIES:
        raise HTTPException(status_code=400, detail=error_detail)

    validated: Dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise HTTPException(status_code=400, detail=error_detail)
        if len(key) == 0 or len(key) > MAX_PROPERTY_KEY_LENGTH:
            raise HTTPException(status_code=400, detail=error_detail)
        if len(value) > MAX_PROPERTY_VALUE_LENGTH:
            raise HTTPException(status_code=400, detail=error_detail)
        validated[key] = value

    name = validated.get("name")
    if name is None or len(name) == 0 or len(name) > MAX_NAME_LENGTH:
        raise HTTPException(status_code=400, detail=error_detail)

    serialized = json.dumps(validated, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_SERIALIZED_PRODUCT_SIZE:
        raise HTTPException(status_code=400, detail=error_detail)

    return validated


class AddProductRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: NameStr

    @model_validator(mode="after")
    def validate_all_fields(self) -> "AddProductRequest":
        data = self.model_dump()
        _validate_product_dict(data, "Invalid input")
        return self


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
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        try:
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, SQLITE_TEXT_LIMIT)
        except AttributeError:
            pass
        yield conn
        conn.commit()
    finally:
        conn.close()


def serialize_product(product: Dict[str, str]) -> str:
    serialized = json.dumps(product, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_SERIALIZED_PRODUCT_SIZE:
        raise HTTPException(status_code=400, detail="Invalid input")
    return serialized


def deserialize_product(data: str) -> Dict[str, str]:
    if len(data.encode("utf-8")) > MAX_UPLOAD_DATA_SIZE:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    return _validate_product_dict(parsed, "Invalid upload query")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post("/add", status_code=201)
async def add_product(product: AddProductRequest):
    product_dict = _validate_product_dict(product.model_dump(), "Invalid input")
    stored_data = serialize_product(product_dict)

    with get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM products WHERE name = ?",
            (product_dict["name"],),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=400, detail="Invalid input")

        conn.execute(
            """
            INSERT INTO products (name, data)
            VALUES (?, ?)
            """,
            (product_dict["name"], stored_data),
        )

    return {"message": "Product successfully added"}


@app.get("/download")
def download_product(name: NameStr = Query(..., description="The name of the product")):
    with get_db() as conn:
        row = conn.execute(
            "SELECT data FROM products WHERE name = ?",
            (name,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=400, detail="Invalid input")

    data = row["data"]
    if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_DOWNLOAD_DATA_SIZE:
        raise HTTPException(status_code=400, detail="Invalid input")

    return {"data": data}


@app.post("/upload")
async def upload_product(payload: UploadRequest):
    product = deserialize_product(payload.data)

    if product["name"] != payload.name:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    normalized_data = json.dumps(product, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(normalized_data.encode("utf-8")) > MAX_SERIALIZED_PRODUCT_SIZE:
        raise HTTPException(status_code=400, detail="Invalid upload query")

    with get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO products (name, data)
            VALUES (?, ?)
            """,
            (payload.name, normalized_data),
        )

    return {"message": "The product with the given name was updated."}


@app.get("/search")
def search_products(
    query: QueryStr = Query(..., description="A search query to find matching products.")
):
    query_lower = query.lower()
    results: List[Dict[str, str]] = []

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT data
            FROM products
            WHERE instr(lower(name), lower(?)) > 0
               OR instr(lower(data), lower(?)) > 0
            LIMIT ?
            """,
            (query, query, MAX_SEARCH_RESULTS),
        ).fetchall()

    for row in rows:
        data = row["data"]
        if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_SERIALIZED_PRODUCT_SIZE:
            continue

        try:
            product = json.loads(data)
        except json.JSONDecodeError:
            continue

        if not isinstance(product, dict):
            continue

        valid_product = True
        normalized_product: Dict[str, str] = {}
        for key, value in product.items():
            if not isinstance(key, str) or not isinstance(value, str):
                valid_product = False
                break
            normalized_product[key] = value

        if not valid_product or "name" not in normalized_product:
            continue

        matched = False
        for key, value in normalized_product.items():
            if query_lower in key.lower() or query_lower in value.lower():
                matched = True
                break

        if matched:
            results.append(normalized_product)

    return {"results": results}


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)