import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import Generator, List

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"
MAX_ABSOLUTE_COUNT = 1_000_000


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
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
                count INTEGER NOT NULL CHECK (count >= 0 AND count <= 2147483647),
                PRIMARY KEY (cart_id, item_id),
                FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
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
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        conn.close()


class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")


class AddToCartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_id: str = Field(..., description="The unique identifier of the cart")
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The number of items to add (or remove if negative)")


class RetrieveCartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_id: str = Field(..., description="The unique identifier of the cart")


class CartItemResponse(BaseModel):
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")


class RetrieveCartResponse(BaseModel):
    items: List[CartItemResponse]


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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request"},
    )


@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createCart",
    summary="Create a new shopping cart",
)
def create_cart() -> CreateCartResponse:
    cart_id = str(uuid.uuid4())

    with get_db() as conn:
        conn.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        conn.commit()

    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    response_model=MessageResponse,
    operation_id="addToCart",
    summary="Add or remove items from the cart",
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "Cart not found"},
    },
)
def add_to_cart(payload: AddToCartRequest) -> MessageResponse:
    if payload.count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="count must not be zero",
        )

    if abs(payload.count) > MAX_ABSOLUTE_COUNT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="count is out of allowed range",
        )

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
                detail="resulting item count cannot be negative",
            )

        if new_count > MAX_ABSOLUTE_COUNT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="resulting item count is out of allowed range",
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

        conn.commit()

    return MessageResponse(message="Item added or removed successfully")


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    operation_id="retrieveCart",
    summary="Retrieve all items in the cart",
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "Cart not found"},
    },
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