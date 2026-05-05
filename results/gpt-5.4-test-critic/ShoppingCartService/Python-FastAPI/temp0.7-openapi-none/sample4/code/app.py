import os
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Generator

import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "db.sqlite3"
SQLITE_TIMEOUT_SECONDS = 30.0
MAX_ITEM_COUNT = 1_000_000


def _create_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=SQLITE_TIMEOUT_SECONDS,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _create_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cart_items (
                cart_id TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                count INTEGER NOT NULL CHECK (count >= 0 AND count <= 1000000),
                PRIMARY KEY (cart_id, item_id),
                FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
            )
            """
        )
    finally:
        conn.close()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = _create_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class CreateCartResponse(BaseModel):
    cart_id: str


class AddToCartRequest(BaseModel):
    cart_id: str
    item_id: int = Field(...)
    count: int = Field(...)

    @field_validator("item_id")
    @classmethod
    def validate_item_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("item_id must be a positive integer")
        return value

    @field_validator("count")
    @classmethod
    def validate_count(cls, value: int) -> int:
        if value == 0:
            raise ValueError("count must not be zero")
        if value < -MAX_ITEM_COUNT or value > MAX_ITEM_COUNT:
            raise ValueError("count out of allowed range")
        return value


class RetrieveCartRequest(BaseModel):
    cart_id: str


class CartItemResponse(BaseModel):
    item_id: int
    count: int


class RetrieveCartResponse(BaseModel):
    items: list[CartItemResponse]

    model_config = ConfigDict(from_attributes=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Shopping Cart Service",
    description="A simple shopping cart service API.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new shopping cart",
    operation_id="createCart",
)
def create_cart() -> CreateCartResponse:
    cart_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    status_code=status.HTTP_200_OK,
    summary="Add or remove items from the cart",
    operation_id="addToCart",
)
def add_to_cart(payload: AddToCartRequest) -> dict:
    with get_db() as conn:
        cart = conn.execute(
            "SELECT cart_id FROM carts WHERE cart_id = ?",
            (payload.cart_id,),
        ).fetchone()

        if cart is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cart not found",
            )

        current_row = conn.execute(
            """
            SELECT count
            FROM cart_items
            WHERE cart_id = ? AND item_id = ?
            """,
            (payload.cart_id, payload.item_id),
        ).fetchone()

        current_count = current_row["count"] if current_row is not None else 0
        new_count = current_count + payload.count

        if new_count < 0 or new_count > MAX_ITEM_COUNT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request",
            )

        if new_count == 0:
            conn.execute(
                """
                DELETE FROM cart_items
                WHERE cart_id = ? AND item_id = ?
                """,
                (payload.cart_id, payload.item_id),
            )
        elif current_row is None:
            conn.execute(
                """
                INSERT INTO cart_items (cart_id, item_id, count)
                VALUES (?, ?, ?)
                """,
                (payload.cart_id, payload.item_id, new_count),
            )
        else:
            conn.execute(
                """
                UPDATE cart_items
                SET count = ?
                WHERE cart_id = ? AND item_id = ?
                """,
                (new_count, payload.cart_id, payload.item_id),
            )

    return {"message": "Item added or removed successfully"}


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all items in the cart",
    operation_id="retrieveCart",
)
def retrieve_cart(payload: RetrieveCartRequest) -> RetrieveCartResponse:
    with get_db() as conn:
        cart = conn.execute(
            "SELECT cart_id FROM carts WHERE cart_id = ?",
            (payload.cart_id,),
        ).fetchone()

        if cart is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cart not found",
            )

        rows = conn.execute(
            """
            SELECT item_id, count
            FROM cart_items
            WHERE cart_id = ?
            ORDER BY item_id
            """,
            (payload.cart_id,),
        ).fetchall()

    items = [CartItemResponse(item_id=row["item_id"], count=row["count"]) for row in rows]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)