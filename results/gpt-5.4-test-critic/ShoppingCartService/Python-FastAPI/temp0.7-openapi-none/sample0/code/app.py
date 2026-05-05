import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import Generator, List

import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field


DATABASE_PATH = "db.sqlite3"
MAX_ITEM_ID = 2_147_483_647
MAX_COUNT_DELTA = 1_000_000
MAX_ITEM_COUNT = 1_000_000


def init_db() -> None:
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
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
                count INTEGER NOT NULL,
                PRIMARY KEY (cart_id, item_id),
                FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE,
                CHECK (item_id >= 1 AND item_id <= 2147483647),
                CHECK (count >= 1 AND count <= 1000000)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DATABASE_PATH)
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


class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")


class AddToCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")
    item_id: int = Field(
        ...,
        ge=1,
        le=MAX_ITEM_ID,
        description="The unique identifier of the item",
    )
    count: int = Field(
        ...,
        ge=-MAX_COUNT_DELTA,
        le=MAX_COUNT_DELTA,
        description="The number of items to add (or remove if negative)",
    )


class RetrieveCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")


class CartItemResponse(BaseModel):
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")


class RetrieveCartResponse(BaseModel):
    items: List[CartItemResponse]


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


@app.post("/create_cart", response_model=CreateCartResponse, status_code=status.HTTP_201_CREATED)
def create_cart() -> CreateCartResponse:
    cart_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
    return CreateCartResponse(cart_id=cart_id)


@app.post("/add_to_cart", status_code=status.HTTP_200_OK)
def add_to_cart(request: AddToCartRequest):
    if request.count == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="count must not be zero")

    with get_db() as conn:
        cart = conn.execute(
            "SELECT cart_id FROM carts WHERE cart_id = ?",
            (request.cart_id,),
        ).fetchone()

        if cart is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart not found")

        existing_item = conn.execute(
            """
            SELECT count
            FROM cart_items
            WHERE cart_id = ? AND item_id = ?
            """,
            (request.cart_id, request.item_id),
        ).fetchone()

        current_count = existing_item["count"] if existing_item is not None else 0
        new_count = current_count + request.count

        if new_count < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resulting item count cannot be negative",
            )

        if new_count > MAX_ITEM_COUNT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resulting item count exceeds maximum allowed",
            )

        if new_count == 0:
            conn.execute(
                "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                (request.cart_id, request.item_id),
            )
        elif existing_item is None:
            conn.execute(
                """
                INSERT INTO cart_items (cart_id, item_id, count)
                VALUES (?, ?, ?)
                """,
                (request.cart_id, request.item_id, new_count),
            )
        else:
            conn.execute(
                """
                UPDATE cart_items
                SET count = ?
                WHERE cart_id = ? AND item_id = ?
                """,
                (new_count, request.cart_id, request.item_id),
            )

    return {"message": "Item added or removed successfully"}


@app.post("/retrieve_cart", response_model=RetrieveCartResponse, status_code=status.HTTP_200_OK)
def retrieve_cart(request: RetrieveCartRequest) -> RetrieveCartResponse:
    with get_db() as conn:
        cart = conn.execute(
            "SELECT cart_id FROM carts WHERE cart_id = ?",
            (request.cart_id,),
        ).fetchone()

        if cart is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart not found")

        rows = conn.execute(
            """
            SELECT item_id, count
            FROM cart_items
            WHERE cart_id = ?
            ORDER BY item_id ASC
            """,
            (request.cart_id,),
        ).fetchall()

    items = [CartItemResponse(item_id=row["item_id"], count=row["count"]) for row in rows]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)