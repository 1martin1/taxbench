import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

DB_PATH = "db.sqlite3"
MAX_ITEM_ID = 2_147_483_647
MAX_ITEM_COUNT = 2_147_483_647


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cart_items (
                cart_id TEXT NOT NULL,
                item_id INTEGER NOT NULL CHECK (item_id >= 1 AND item_id <= 2147483647),
                count INTEGER NOT NULL CHECK (count > 0 AND count <= 2147483647),
                PRIMARY KEY (cart_id, item_id),
                FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
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
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate_item_id(item_id: int) -> None:
    if not (1 <= item_id <= MAX_ITEM_ID):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"item_id must be between 1 and {MAX_ITEM_ID}",
        )


def validate_count(count: int) -> None:
    if count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="count must not be zero",
        )
    if not (-MAX_ITEM_COUNT <= count <= MAX_ITEM_COUNT):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"count must be between {-MAX_ITEM_COUNT} and {MAX_ITEM_COUNT}, excluding zero",
        )


class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")


class AddToCartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_id: str = Field(..., min_length=1, description="The unique identifier of the cart")
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The number of items to add (or remove if negative)")


class RetrieveCartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_id: str = Field(..., min_length=1, description="The unique identifier of the cart")


class CartItem(BaseModel):
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")


class RetrieveCartResponse(BaseModel):
    items: List[CartItem]


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


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
def create_cart():
    cart_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Add or remove items from the cart",
    operation_id="addToCart",
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "Cart not found"},
    },
)
def add_to_cart(payload: AddToCartRequest):
    validate_item_id(payload.item_id)
    validate_count(payload.count)

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

        existing = conn.execute(
            "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
            (payload.cart_id, payload.item_id),
        ).fetchone()

        current_count = existing["count"] if existing is not None else 0
        new_count = current_count + payload.count

        if new_count < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resulting item count cannot be negative",
            )

        if new_count > MAX_ITEM_COUNT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Resulting item count cannot exceed {MAX_ITEM_COUNT}",
            )

        if new_count == 0:
            conn.execute(
                "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                (payload.cart_id, payload.item_id),
            )
        elif existing is None:
            conn.execute(
                "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                (payload.cart_id, payload.item_id, new_count),
            )
        else:
            conn.execute(
                "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                (new_count, payload.cart_id, payload.item_id),
            )

    return MessageResponse(message="Item added or removed successfully")


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all items in the cart",
    operation_id="retrieveCart",
    responses={
        404: {"description": "Cart not found"},
    },
)
def retrieve_cart(payload: RetrieveCartRequest):
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
            ORDER BY item_id ASC
            """,
            (payload.cart_id,),
        ).fetchall()

    items = [CartItem(item_id=row["item_id"], count=row["count"]) for row in rows]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)