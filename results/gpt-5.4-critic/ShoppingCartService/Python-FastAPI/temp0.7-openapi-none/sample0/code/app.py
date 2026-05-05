import sqlite3
import uuid
from contextlib import contextmanager
from typing import Generator, List
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator


DB_PATH = "db.sqlite3"

MAX_REQUEST_BODY_BYTES = 1024
MAX_TOTAL_CARTS = 10000
MAX_ITEMS_PER_CART = 1000
MAX_ITEM_COUNT = 1_000_000
MAX_RETRIEVE_ITEMS = 1000


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db_cursor() -> Generator[sqlite3.Cursor, None, None]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db_cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS cart_items (
                cart_id TEXT NOT NULL,
                item_id INTEGER NOT NULL CHECK (item_id > 0),
                count INTEGER NOT NULL CHECK (count > 0 AND count <= 1000000),
                PRIMARY KEY (cart_id, item_id),
                FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cart_items_cart_id ON cart_items(cart_id)"
        )


def validate_cart_id(value: str) -> str:
    try:
        parsed = UUID(value, version=4)
    except (ValueError, AttributeError, TypeError):
        raise ValueError("cart_id must be a valid UUID")
    return str(parsed)


class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")


class AddToCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")
    item_id: int = Field(..., gt=0, description="The unique identifier of the item")
    count: int = Field(
        ...,
        ge=-MAX_ITEM_COUNT,
        le=MAX_ITEM_COUNT,
        description="The number of items to add (or remove if negative)",
    )

    @field_validator("cart_id")
    @classmethod
    def validate_cart_id_field(cls, value: str) -> str:
        return validate_cart_id(value)


class RetrieveCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")

    @field_validator("cart_id")
    @classmethod
    def validate_cart_id_field(cls, value: str) -> str:
        return validate_cart_id(value)


class CartItemResponse(BaseModel):
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")


class RetrieveCartResponse(BaseModel):
    items: List[CartItemResponse]


app = FastAPI(
    title="Shopping Cart Service",
    description="A simple shopping cart service API.",
    version="1.0.0",
)


@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return Response(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        except ValueError:
            return Response(status_code=status.HTTP_400_BAD_REQUEST)

    body = await request.body()
    if len(body) > MAX_REQUEST_BODY_BYTES:
        return Response(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(request.scope, receive)
    return await call_next(request)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new shopping cart",
    operation_id="createCart",
)
def create_cart() -> CreateCartResponse:
    cart_id = str(uuid.uuid4())

    with db_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS total FROM carts")
        total_carts = int(cursor.fetchone()["total"])
        if total_carts >= MAX_TOTAL_CARTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cart limit reached",
            )

        cursor.execute(
            "INSERT INTO carts (cart_id) VALUES (?)",
            (cart_id,),
        )

    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    status_code=status.HTTP_200_OK,
    summary="Add or remove items from the cart",
    operation_id="addToCart",
    responses={
        200: {"description": "Item added or removed successfully"},
        400: {"description": "Invalid request"},
        404: {"description": "Cart not found"},
    },
)
def add_to_cart(payload: AddToCartRequest) -> Response:
    if payload.count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="count must not be zero",
        )

    with db_cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM carts WHERE cart_id = ?",
            (payload.cart_id,),
        )
        if cursor.fetchone() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cart not found",
            )

        cursor.execute(
            "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
            (payload.cart_id, payload.item_id),
        )
        existing_row = cursor.fetchone()
        existing_count = int(existing_row["count"]) if existing_row is not None else 0
        new_count = existing_count + payload.count

        if new_count < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resulting item count cannot be negative",
            )

        if new_count > MAX_ITEM_COUNT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resulting item count exceeds limit",
            )

        if new_count == 0:
            cursor.execute(
                "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                (payload.cart_id, payload.item_id),
            )
        elif existing_row is None:
            cursor.execute(
                "SELECT COUNT(*) AS total FROM cart_items WHERE cart_id = ?",
                (payload.cart_id,),
            )
            item_total = int(cursor.fetchone()["total"])
            if item_total >= MAX_ITEMS_PER_CART:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cart item limit reached",
                )

            cursor.execute(
                "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                (payload.cart_id, payload.item_id, new_count),
            )
        else:
            cursor.execute(
                "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                (new_count, payload.cart_id, payload.item_id),
            )

    return Response(status_code=status.HTTP_200_OK)


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    summary="Retrieve all items in the cart",
    operation_id="retrieveCart",
    responses={
        404: {"description": "Cart not found"},
    },
)
def retrieve_cart(payload: RetrieveCartRequest) -> RetrieveCartResponse:
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM carts WHERE cart_id = ?",
            (payload.cart_id,),
        )
        if cursor.fetchone() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cart not found",
            )

        cursor.execute(
            """
            SELECT item_id, count
            FROM cart_items
            WHERE cart_id = ?
            ORDER BY item_id ASC
            LIMIT ?
            """,
            (payload.cart_id, MAX_RETRIEVE_ITEMS),
        )
        rows = cursor.fetchall()

    items = [CartItemResponse(item_id=row["item_id"], count=row["count"]) for row in rows]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)